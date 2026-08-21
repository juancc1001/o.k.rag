"""OAuth 2.1 resource-server auth for `okragged mcp --transport http` (DESIGN.md
§13 M8, criterion 1).

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
"""

from __future__ import annotations

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
