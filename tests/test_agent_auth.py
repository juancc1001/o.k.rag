"""`JWKSTokenVerifier` (DESIGN.md §13 M8, criterion 1: OAuth 2.1 reads over
streamable-HTTP) - the offline unit tests against a locally generated test
keypair, plus the wired-up `MCPServer` app driven in-process (no real socket).

Criterion 2 - "the bundle is reachable only through tools, never as files" -
needs no new mounting logic; `test_the_http_app_mounts_no_bundle_or_static_route`
is the offline check DESIGN.md asks for instead of a convention.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from okragged.agent.auth import JWKSTokenVerifier
from okragged.config import Config, McpConfig

ISSUER = "https://issuer.example.com"
RESOURCE = "https://mcp.example.com"
JWKS_URL = "https://issuer.example.com/.well-known/jwks.json"


@pytest.fixture(scope="module")
def keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


class StubJWKClient:
    """Returns a fixed key regardless of the token's `kid` - no network call."""

    def __init__(self, key) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=self._key)


def mcp_config(**overrides) -> McpConfig:
    defaults = dict(
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        jwks_url=JWKS_URL,
        required_scopes=[],
    )
    defaults.update(overrides)
    return McpConfig(**defaults)


def make_token(
    private_key,
    *,
    issuer: str = ISSUER,
    audience: str = RESOURCE,
    scope: str = "read",
    exp_delta: int = 3600,
    **extra_claims,
) -> str:
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": "user-1",
        "iat": now,
        "exp": now + exp_delta,
        "scope": scope,
    }
    payload.update(extra_claims)
    return jwt.encode(payload, private_key, algorithm="RS256")


def verify(keys, token: str, **config_overrides):
    import anyio

    _, public_key = keys
    verifier = JWKSTokenVerifier(mcp_config(**config_overrides), jwk_client=StubJWKClient(public_key))
    return anyio.run(lambda: verifier.verify_token(token))


# --- JWKSTokenVerifier.verify_token, offline -------------------------------------


def test_a_validly_signed_token_is_accepted(keys):
    private_key, _ = keys
    result = verify(keys, make_token(private_key))
    assert result is not None
    assert result.subject == "user-1"
    assert result.scopes == ["read"]
    assert result.resource == RESOURCE


def test_a_token_signed_by_a_different_key_is_rejected(keys):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = make_token(other_key)
    assert verify(keys, forged) is None


def test_a_token_with_the_wrong_issuer_is_rejected(keys):
    private_key, _ = keys
    token = make_token(private_key, issuer="https://someone-else.example.com")
    assert verify(keys, token) is None


def test_a_token_with_the_wrong_audience_is_rejected(keys):
    private_key, _ = keys
    token = make_token(private_key, audience="https://not-this-server.example.com")
    assert verify(keys, token) is None


def test_an_expired_token_is_rejected(keys):
    private_key, _ = keys
    token = make_token(private_key, exp_delta=-10)
    assert verify(keys, token) is None


def test_a_malformed_token_is_rejected_rather_than_raising(keys):
    assert verify(keys, "not-a-jwt-at-all") is None


def test_a_token_missing_a_required_scope_is_rejected(keys):
    private_key, _ = keys
    token = make_token(private_key, scope="read")
    assert verify(keys, token, required_scopes=["write"]) is None


def test_a_token_carrying_every_required_scope_is_accepted(keys):
    private_key, _ = keys
    token = make_token(private_key, scope="read write")
    result = verify(keys, token, required_scopes=["write"])
    assert result is not None
    assert set(result.scopes) == {"read", "write"}


# --- the wired-up MCPServer app, driven in-process --------------------------------


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(project_root=tmp_path)


def http_app(cfg, keys, monkeypatch, **config_overrides):
    """`build_server(..., http_auth=...)`'s `streamable_http_app()`, with the JWKS
    fetch stubbed to the test keypair - no network call, no real socket."""
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    _, public_key = keys

    from okragged.agent.server import build_server

    # `JWKSTokenVerifier.__init__` builds a real `jwt.PyJWKClient` when no
    # `jwk_client` is passed - `build_server` never passes one, so the stub
    # goes one level lower, on the class the SDK-agnostic verifier defers to.
    monkeypatch.setattr(jwt, "PyJWKClient", lambda *a, **kw: StubJWKClient(public_key))
    server = build_server(cfg, http_auth=mcp_config(**config_overrides))
    return server.streamable_http_app()


def test_an_unauthenticated_request_is_refused_with_a_www_authenticate_header(
    cfg, keys, monkeypatch
):
    from starlette.testclient import TestClient

    client = TestClient(http_app(cfg, keys, monkeypatch))
    response = client.post("/mcp", json={"jsonrpc": "2.0", "method": "ping", "id": 1})

    assert response.status_code == 401
    www_authenticate = response.headers["www-authenticate"]
    assert "resource_metadata=" in www_authenticate
    assert "oauth-protected-resource" in www_authenticate


def test_a_validly_authenticated_request_gets_past_the_auth_gate(cfg, keys, monkeypatch):
    from starlette.testclient import TestClient

    private_key, _ = keys
    token = make_token(private_key)

    # the session manager's lifespan (its task group) only starts inside the
    # `with` block - a bare `TestClient(app)` never fires it
    with TestClient(http_app(cfg, keys, monkeypatch)) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code != 401


def test_the_http_app_mounts_no_bundle_or_static_route(cfg, keys, monkeypatch):
    """Criterion 2, checked by construction: `streamable_http_app()` only ever
    mounts the protocol endpoint plus the auth metadata route it declared -
    never a static file mount. `api/app.py`'s `STATIC_DIR` mount is unrelated
    and lives behind `okragged serve`, not this transport."""
    from starlette.routing import Mount

    app = http_app(cfg, keys, monkeypatch)

    assert not any(isinstance(route, Mount) for route in app.routes)
    paths = sorted(getattr(route, "path", "") for route in app.routes)
    assert paths == ["/.well-known/oauth-protected-resource", "/mcp"]
    assert not any("bundle" in path for path in paths)
