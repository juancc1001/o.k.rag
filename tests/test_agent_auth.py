"""`JWKSTokenVerifier` (DESIGN.md §13 M8, criterion 1: OAuth 2.1 reads over
streamable-HTTP) - the offline unit tests against a locally generated test
keypair, plus the wired-up `MCPServer` app driven in-process (no real socket).

Criterion 2 - "the bundle is reachable only through tools, never as files" -
needs no new mounting logic; `test_the_http_app_mounts_no_bundle_or_static_route`
is the offline check DESIGN.md asks for instead of a convention.

Criterion 4 - identity, a spend scope, and an hourly rate limit for the
synthesizing `add_source` - is tested in two layers, same split as criterion
1's own tests: `authorize_spend`/`SpendRateLimiter` offline, against a
directly-constructed `AccessToken` (no real bearer token, no signature to
verify - that is criterion 1's own tested surface), and the wiring inside
`build_server`'s write+spend tool driven in-process with `mcp.Client`, which
has no HTTP layer of its own to populate the SDK's auth contextvar - so the
gate there is exercised by setting and clearing that contextvar directly,
exactly as the SDK's own `AuthContextMiddleware` would around a real request.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from okragged import config as config_module
from okragged.agent.auth import (
    JWKSTokenVerifier,
    SpendRateLimiter,
    SpendRefused,
    VerifyRefused,
    authorize_spend,
    authorize_verify,
)
from okragged.config import Config, McpConfig
from test_agent import ingestable, project  # noqa: F401  (used as fixtures)

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
        verify_scopes=[],
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


# --- authorize_spend / SpendRateLimiter, offline (DESIGN.md §13 M8, criterion 4) --


def access_token(**overrides):
    from mcp.server.auth.provider import AccessToken

    defaults = dict(
        token="t",
        client_id="client-1",
        scopes=["read"],
        expires_at=int(time.time()) + 3600,
        resource=RESOURCE,
        subject="user-1",
        claims={},
    )
    defaults.update(overrides)
    return AccessToken(**defaults)


def set_access_token(monkeypatch, token) -> None:
    """Stands in for the SDK's own `AuthContextMiddleware`, which sets this
    contextvar around a real authenticated request under `--transport http` -
    `authorize_spend` reads it the same way regardless of who set it."""
    from mcp.server.auth.middleware import auth_context
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    user = AuthenticatedUser(token) if token is not None else None
    monkeypatch.setattr(
        auth_context, "get_access_token", lambda: (user.access_token if user else None)
    )


def test_authorize_spend_refuses_with_no_authenticated_identity_at_all(monkeypatch):
    set_access_token(monkeypatch, None)
    config = mcp_config(spend_scopes=["okragged:spend"], spend_rate_limit_per_hour=5)
    with pytest.raises(SpendRefused):
        authorize_spend(config, SpendRateLimiter(5))


def test_authorize_spend_refuses_a_token_missing_the_spend_scope(monkeypatch):
    set_access_token(monkeypatch, access_token(scopes=["read"]))
    config = mcp_config(spend_scopes=["okragged:spend"], spend_rate_limit_per_hour=5)
    with pytest.raises(SpendRefused):
        authorize_spend(config, SpendRateLimiter(5))


def test_authorize_spend_accepts_and_names_a_token_carrying_every_spend_scope(monkeypatch):
    set_access_token(monkeypatch, access_token(scopes=["read", "okragged:spend"], subject="user-42"))
    config = mcp_config(spend_scopes=["okragged:spend"], spend_rate_limit_per_hour=5)
    assert authorize_spend(config, SpendRateLimiter(5)) == "user-42"


def test_authorize_spend_refuses_once_the_limiter_is_exhausted(monkeypatch):
    set_access_token(monkeypatch, access_token(scopes=["okragged:spend"]))
    config = mcp_config(spend_scopes=["okragged:spend"], spend_rate_limit_per_hour=1)
    limiter = SpendRateLimiter(1)

    assert authorize_spend(config, limiter) == "user-1"
    with pytest.raises(SpendRefused):
        authorize_spend(config, limiter)


def test_spend_rate_limiter_refuses_past_the_hourly_cap_for_one_identity():
    now = [0.0]
    limiter = SpendRateLimiter(2, clock=lambda: now[0])

    assert limiter.allow("user-1")
    assert limiter.allow("user-1")
    assert not limiter.allow("user-1")


def test_spend_rate_limiter_leaves_a_different_identity_unaffected():
    now = [0.0]
    limiter = SpendRateLimiter(1, clock=lambda: now[0])

    assert limiter.allow("user-1")
    assert not limiter.allow("user-1")
    assert limiter.allow("user-2")


def test_spend_rate_limiter_forgets_calls_older_than_an_hour():
    now = [0.0]
    limiter = SpendRateLimiter(1, clock=lambda: now[0])

    assert limiter.allow("user-1")
    assert not limiter.allow("user-1")
    now[0] += 3601
    assert limiter.allow("user-1")


# --- authorize_verify, offline (DESIGN.md §13 M8, criterion 5) --------------------


def test_authorize_verify_refuses_with_no_authenticated_identity_at_all(monkeypatch):
    set_access_token(monkeypatch, None)
    config = mcp_config(verify_scopes=["okragged:verify"])
    with pytest.raises(VerifyRefused):
        authorize_verify(config)


def test_authorize_verify_refuses_a_token_missing_the_verify_scope(monkeypatch):
    set_access_token(monkeypatch, access_token(scopes=["read"]))
    config = mcp_config(verify_scopes=["okragged:verify"])
    with pytest.raises(VerifyRefused):
        authorize_verify(config)


def test_authorize_verify_accepts_and_names_a_token_carrying_every_verify_scope(monkeypatch):
    set_access_token(
        monkeypatch, access_token(scopes=["read", "okragged:verify"], subject="user-42")
    )
    config = mcp_config(verify_scopes=["okragged:verify"])
    assert authorize_verify(config) == "user-42"


# --- the gate wired into build_server's write+spend tool --------------------------


def synth_server(project: Path, **mcp_overrides):
    """`write=True, spend=True` under http, driven in-process with `mcp.Client` -
    no real socket, no ASGI auth middleware, which is exactly why the tests below
    set the SDK's own contextvar directly rather than presenting a bearer token."""
    from okragged.agent.server import build_server

    http_auth = mcp_config(
        spend_scopes=["okragged:spend"], spend_rate_limit_per_hour=5, **mcp_overrides
    )
    return build_server(Config(project_root=project), write=True, spend=True, http_auth=http_auth)


def test_a_refused_spend_writes_nothing_and_never_reaches_add_source(ingestable, monkeypatch):
    import anyio
    from mcp import Client

    from okragged.agent import server as server_module

    def refuse(config, limiter):
        raise SpendRefused("no scope")

    monkeypatch.setattr(server_module, "authorize_spend", refuse)

    async def go():
        async with Client(synth_server(ingestable)) as client:
            return await client.call_tool("add_source", {"text": "Two tables are related."})

    result = anyio.run(go)
    assert result.is_error
    assert not (ingestable / "sources" / "conversations").exists()


def test_an_authorized_spend_records_and_synthesizes_as_usual(ingestable, monkeypatch):
    import anyio
    from mcp import Client

    from okragged.agent import server as server_module

    monkeypatch.setattr(server_module, "authorize_spend", lambda config, limiter: "user-1")

    async def go():
        async with Client(synth_server(ingestable)) as client:
            return await client.call_tool("add_source", {"text": "Two tables are related."})

    result = anyio.run(go)
    assert not result.is_error, result.content[0].text
    assert result.structured_content["synthesized"]
    assert result.structured_content["concepts"]


def test_the_stdio_path_never_calls_authorize_spend(ingestable, monkeypatch):
    """`http_auth is None` under stdio - the gate does not run at all, unchanged
    since M6 item 5's single-party guard (the operator launched the instance with
    both switches, and that is still the whole story there)."""
    import anyio
    from mcp import Client

    from okragged.agent import server as server_module
    from okragged.agent.server import build_server

    called: list[object] = []
    monkeypatch.setattr(
        server_module, "authorize_spend", lambda *a, **kw: called.append(1) or "unused"
    )

    async def go():
        cfg = Config(project_root=ingestable)
        async with Client(build_server(cfg, write=True, spend=True)) as client:
            return await client.call_tool("add_source", {"text": "Two tables are related."})

    result = anyio.run(go)
    assert not result.is_error, result.content[0].text
    assert called == []


# --- the verify_concept tool, wired (DESIGN.md §13 M8, criterion 5) ---------------


def verify_server(project: Path, *, write: bool = True, http_auth=None, **mcp_overrides):
    """Mirrors `synth_server`'s shape for `verify_concept` rather than `add_source`.
    `http_auth=None` reproduces stdio; passing one reproduces http with whatever
    `verify_scopes` the caller wants to exercise."""
    from okragged.agent.server import build_server

    return build_server(Config(project_root=project), write=write, http_auth=http_auth)


def test_verify_concept_absent_when_write_is_off(project):
    import anyio
    from mcp import Client

    http_auth = mcp_config(verify_scopes=["okragged:verify"])

    async def go():
        async with Client(verify_server(project, write=False, http_auth=http_auth)) as client:
            return sorted(tool.name for tool in (await client.list_tools()).tools)

    assert "verify_concept" not in anyio.run(go)


def test_verify_concept_absent_when_verify_scopes_is_empty(project):
    """Empty `verify_scopes` reads as "this deployment doesn't offer verification",
    not a misconfiguration - there is no CLI flag of its own to fail fast on."""
    import anyio
    from mcp import Client

    http_auth = mcp_config(verify_scopes=[])

    async def go():
        async with Client(verify_server(project, write=True, http_auth=http_auth)) as client:
            return sorted(tool.name for tool in (await client.list_tools()).tools)

    assert "verify_concept" not in anyio.run(go)


def test_verify_concept_absent_under_stdio_regardless_of_flags(project):
    """`http_auth is None` under stdio - there is no identity to attribute an entry
    to, so the tool never exists there, `--write` or not."""
    import anyio
    from mcp import Client

    async def go():
        async with Client(verify_server(project, write=True, http_auth=None)) as client:
            return sorted(tool.name for tool in (await client.list_tools()).tools)

    assert "verify_concept" not in anyio.run(go)


def test_a_refused_verify_writes_nothing(project, monkeypatch):
    import anyio
    from mcp import Client

    from okragged.agent import server as server_module

    def refuse(config):
        raise VerifyRefused("no scope")

    monkeypatch.setattr(server_module, "authorize_verify", refuse)
    before = (project / "bundle" / "revenue-policy.md").read_text(encoding="utf-8-sig")
    http_auth = mcp_config(verify_scopes=["okragged:verify"])

    async def go():
        async with Client(verify_server(project, http_auth=http_auth)) as client:
            return await client.call_tool("verify_concept", {"concept_id": "revenue-policy"})

    result = anyio.run(go)
    assert result.is_error
    after = (project / "bundle" / "revenue-policy.md").read_text(encoding="utf-8-sig")
    assert after == before


def test_an_authorized_verify_appends_the_entry_and_the_file_reflects_it(project, monkeypatch):
    import anyio
    from mcp import Client

    from okragged import okf
    from okragged.agent import server as server_module

    monkeypatch.setattr(server_module, "authorize_verify", lambda config: "user-42")
    http_auth = mcp_config(verify_scopes=["okragged:verify"])

    async def go():
        async with Client(verify_server(project, http_auth=http_auth)) as client:
            return await client.call_tool("verify_concept", {"concept_id": "revenue-policy"})

    result = anyio.run(go)
    assert not result.is_error, result.content[0].text
    assert result.structured_content["by"] == "human:user-42"

    on_disk = okf.parse(
        (project / "bundle" / "revenue-policy.md").read_text(encoding="utf-8-sig"),
        "revenue-policy",
    )
    assert on_disk.verified == [{"by": "human:user-42", "at": result.structured_content["at"]}]
    assert on_disk.status == "draft"
