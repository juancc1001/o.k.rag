"""OAuth 2.1 resource-server auth for `okragged mcp --transport http` (DESIGN.md
§13 M8, criterion 1), plus the identity-scoped spend gate criterion 4 builds on
top of it.

okragged never issues tokens - it only verifies ones an external authorization
server issued, against that issuer's published JWKS. `JWKSTokenVerifier`
implements the MCP SDK's `TokenVerifier` protocol (`verify_token(token) ->
AccessToken | None`) and is handed to `MCPServer(token_verifier=...)` alongside
an `AuthSettings`, which is what makes the server run as a pure resource
server: no `/authorize` or `/token` endpoints, only bearer-token verification
and the `.well-known/oauth-protected-resource` metadata route.

No signature, issuer, audience, expiry, or scope check is skippable: any
`jwt.PyJWTError` - bad signature, wrong `iss`/`aud`, expired, malformed -
becomes `None`, exactly like an absent token.

**Criterion 4 turns that verified token into the two things M6 left unbuilt.**
M6 items 1 and 3 - an identity to attribute a call to, and spending as a scope
plus an hourly rate limit rather than a per-call cap - bind where there is a
channel and a host holding the only key, which is here. `AccessToken.subject`/
`client_id` is a claim the caller cannot forge (an external issuer signed it),
unlike M5/M6's single-party case where the caller and the operator are the
same person and a token presented is one that could also be issued. `authorize_spend`
and `SpendRateLimiter` are the identity and the rate limit; `require_http_spend_config`
is the fail-fast pre-flight the two `spend_scopes`/`spend_rate_limit_per_hour`
keys need, the same shape `require_http_auth_config` already gives the three
`[mcp]` URLs.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ..config import McpConfig

# `pyjwt` is pinned in the `mcp`/`dev` extras, not the base install, so this
# module keeps its own import lazy - the same reason `_mcp_server()` defers the
# SDK: `okragged mcp` (plain stdio) must keep working with neither extra
# installed, and this module's exception is imported unconditionally by `cli.py`.


class MissingAuthConfig(Exception):
    """`--transport http` with `[mcp]` absent or incomplete in `okragged.toml`."""


def require_http_auth_config(config: McpConfig) -> None:
    """Raises `MissingAuthConfig` unless every key `--transport http` needs is set.

    The single source of truth for both callers: `cli.py` calls this before
    printing its "serving..." line, so a misconfigured `[mcp]` never claims to
    be serving before failing; `agent/server.py::main` calls it again for
    callers that reach it directly rather than through the CLI.
    """
    missing = [
        name
        for name, value in (
            ("issuer_url", config.issuer_url),
            ("resource_server_url", config.resource_server_url),
            ("jwks_url", config.jwks_url),
        )
        if not value
    ]
    if missing:
        raise MissingAuthConfig(
            f"okragged.toml is missing [mcp].{', [mcp].'.join(missing)} - "
            "http transport needs all three set"
        )


class JWKSTokenVerifier:
    """Verifies a bearer token's signature and claims against the issuer's JWKS.

    `jwk_client` exists so tests can inject a stub returning a locally
    generated test key instead of `PyJWKClient` fetching a real URL - the same
    "inject the dependency, verify offline" shape the `synth`/`vectors`
    provider stubs already use.
    """

    def __init__(self, config: McpConfig, *, jwk_client: Any = None) -> None:
        import jwt

        self._config = config
        self._jwk_client = jwk_client or jwt.PyJWKClient(config.jwks_url)

    async def verify_token(self, token: str):
        import jwt
        from mcp.server.auth.provider import AccessToken

        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                issuer=self._config.issuer_url,
                audience=self._config.resource_server_url,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            return None

        scopes = claims.get("scope", "").split()
        if self._config.required_scopes and not set(self._config.required_scopes) <= set(scopes):
            return None

        return AccessToken(
            token=token,
            client_id=claims.get("client_id") or claims.get("azp") or claims.get("sub", ""),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self._config.resource_server_url,
            subject=claims.get("sub"),
            claims=claims,
        )


def require_http_spend_config(config: McpConfig) -> None:
    """Raises `MissingAuthConfig` unless both `spend_scopes` and
    `spend_rate_limit_per_hour` are set.

    Mirrors `require_http_auth_config`'s shape: `cli.py` calls this before
    printing its "serving..." line, and only when `--write --spend` are both
    asked for under `--transport http` - plain `--write`, or `--spend` alone
    (which only lets `search` embed a query on the host's key, unchanged since
    M6), need neither of these keys.
    """
    missing = []
    if not config.spend_scopes:
        missing.append("spend_scopes")
    if not config.spend_rate_limit_per_hour:
        missing.append("spend_rate_limit_per_hour")
    if missing:
        raise MissingAuthConfig(
            f"okragged.toml is missing [mcp].{', [mcp].'.join(missing)} - "
            "a synthesizing add_source under http transport needs both set"
        )


class SpendRefused(Exception):
    """Raised before `add_source` writes anything - an unscoped or
    over-limit caller under `--transport http`. Safe to raise at this point,
    the same footing as the pre-write `ValueError`s `write.py::add_source`
    already raises for empty text or a concept-shaped submission: nothing
    has touched the corpus yet."""


class SpendRateLimiter:
    """Bounds a spending tool by identity per hour, not by call - money
    follows the configured model and the number of sections a source becomes,
    so a per-call cap bounds nothing (DESIGN.md §13 M6 item 3, M8 criterion 4).

    In-process and unpersisted, on purpose: the identity comes from a verified
    JWT claim the caller cannot forge, and - unlike M5/M6's single-party case,
    where the caller and the operator are the same person and could delete a
    counter kept beside the bundle - a remote caller here has no filesystem
    access to this host at all. Memory that dies with the process is enough to
    close that gap; there is nothing left for a restart to lose that the
    caller could otherwise have taken away.

    `clock` is injectable so tests can simulate an hour passing without
    sleeping - the same "inject the dependency, verify offline" shape
    `JWKSTokenVerifier`'s `jwk_client` param already uses. One instance is
    built per running server (`agent/server.py::build_server`) and closed over
    by the tool, so it accumulates across calls in that process - a rate limit
    that survives a loop, not one reset by the next call.
    """

    _WINDOW_SECONDS = 3600

    def __init__(self, max_per_hour: int, *, clock: Any = time.monotonic) -> None:
        self._max = max_per_hour
        self._clock = clock
        self._calls: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, identity: str) -> bool:
        now = self._clock()
        cutoff = now - self._WINDOW_SECONDS
        with self._lock:
            window = [t for t in self._calls.get(identity, ()) if t >= cutoff]
            if len(window) >= self._max:
                self._calls[identity] = window
                return False
            window.append(now)
            self._calls[identity] = window
            return True


def authorize_spend(config: McpConfig, limiter: SpendRateLimiter) -> str:
    """Raises `SpendRefused` unless the calling identity may spend right now;
    returns the identity the call is charged to otherwise.

    Only ever called when `http_auth is not None` (`agent/server.py`), so the
    absent-token case is a defensive check, not a path this is meant to take -
    under stdio nothing calls this at all, and M6 item 5's single-party guard
    (the operator launched the instance with both switches) still stands there
    unchanged.
    """
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    if token is None:
        raise SpendRefused("no authenticated identity for this call")
    if config.spend_scopes and not set(config.spend_scopes) <= set(token.scopes):
        raise SpendRefused(
            f"the token is missing a required spend scope: {config.spend_scopes}"
        )
    identity = token.subject or token.client_id
    if not limiter.allow(identity):
        raise SpendRefused(
            f"{identity!r} has exceeded {config.spend_rate_limit_per_hour} "
            "spend(s) per hour - try again later"
        )
    return identity


class VerifyRefused(Exception):
    """Raised before `verify_concept` writes anything - an unscoped caller, or
    no authenticated identity at all. Safe to raise at this point, the same
    footing as `SpendRefused`: nothing has touched the bundle yet."""


def authorize_verify(config: McpConfig) -> str:
    """Raises `VerifyRefused` unless the calling identity may verify; returns
    the identity the entry is attributed to otherwise.

    Mirrors `authorize_spend` minus the rate limiter - verifying costs nothing,
    so there is nothing to bound per hour. Only ever called when `http_auth is
    not None` (`agent/server.py`), so the absent-token case is defensive here
    too: under stdio nothing calls this at all, and the single local operator
    still promotes a concept by hand-editing the file, unchanged since M5.
    """
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    if token is None:
        raise VerifyRefused("no authenticated identity for this call")
    if config.verify_scopes and not set(config.verify_scopes) <= set(token.scopes):
        raise VerifyRefused(
            f"the token is missing a required verify scope: {config.verify_scopes}"
        )
    return token.subject or token.client_id
