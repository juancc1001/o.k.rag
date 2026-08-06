"""Anthropic provider.

Credentials resolve through the SDK's own chain (ANTHROPIC_API_KEY,
ANTHROPIC_AUTH_TOKEN, an `ant auth login` profile, workload identity), so the
client is built with no explicit key.
"""

from __future__ import annotations

import os

from ..base import ProviderHTTPError, ProviderResponse, Refused, SynthError

NAME = "anthropic"
DEFAULT_MODEL = "claude-opus-5"
ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def credential_present() -> bool:
    if any(os.environ.get(v) for v in ENV_VARS):
        return True
    # an `ant auth login` profile is equally valid
    config_dir = os.environ.get("ANTHROPIC_CONFIG_DIR")
    base = config_dir or os.path.expanduser("~/.config/anthropic")
    return os.path.isdir(os.path.join(base, "credentials"))


def list_models() -> list[str]:
    import anthropic

    return [m.id for m in anthropic.Anthropic().models.list()]


def call(
    *,
    system: str,
    user: str,
    schema: dict,
    model: str,
    effort: str,
    max_tokens: int,
) -> ProviderResponse:
    import anthropic

    try:
        response = anthropic.Anthropic().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                # cached prefix — `system` must stay byte-identical across the run
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIError as exc:
        # APIStatusError carries the code; connection/timeout errors carry none
        raise ProviderHTTPError(NAME, getattr(exc, "status_code", None), str(exc)) from exc

    if response.stop_reason == "refusal":
        details = response.stop_details
        raise Refused(
            NAME,
            getattr(details, "category", None),
            getattr(details, "explanation", None),
        )
    if response.stop_reason == "max_tokens":
        raise SynthError(f"hit max_tokens ({max_tokens}); the unit is too large to synthesize")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise SynthError("response contained no text block")

    usage = response.usage
    return ProviderResponse(
        text=text,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
    )
