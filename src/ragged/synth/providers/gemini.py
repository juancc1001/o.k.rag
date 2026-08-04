"""Google Gemini provider (google-genai SDK).

Differences from the Anthropic path that matter here:
  - the system prompt is `config.system_instruction`, not a message
  - schema enforcement is `response_json_schema` + `response_mime_type`
  - there is no prompt-caching breakpoint to set; implicit caching is reported
    back via usage_metadata.cached_content_token_count
  - `effort` has no equivalent, so thinking is left at the model default
  - a decline surfaces as a finish_reason or a prompt block_reason, not a
    dedicated stop_reason
"""

from __future__ import annotations

import os

from ..base import MissingCredential, ProviderResponse, Refused, SynthError

NAME = "gemini"
DEFAULT_MODEL = "gemini-2.5-pro"
ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

# finish_reason values that mean "declined", not "failed"
_REFUSAL_REASONS = {
    "SAFETY",
    "PROHIBITED_CONTENT",
    "BLOCKLIST",
    "SPII",
    "RECITATION",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
}


def api_key() -> str:
    for var in ENV_VARS:
        if value := os.environ.get(var):
            return value
    raise MissingCredential(
        f"no Gemini credential: set {ENV_VARS[0]} (or {ENV_VARS[1]}) in .env or the environment"
    )


def credential_present() -> bool:
    return any(os.environ.get(v) for v in ENV_VARS)


def list_models() -> list[str]:
    from google import genai

    client = genai.Client(api_key=api_key())
    names = []
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            names.append((model.name or "").removeprefix("models/"))
    return [n for n in names if n]


def call(
    *,
    system: str,
    user: str,
    schema: dict,
    model: str,
    effort: str,
    max_tokens: int,
) -> ProviderResponse:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key())
    response = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_tokens,
        ),
    )

    _raise_if_blocked(response, max_tokens)

    text = response.text
    if not text:
        raise SynthError("response contained no text")

    usage = response.usage_metadata
    return ProviderResponse(
        text=text,
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
    )


def _raise_if_blocked(response, max_tokens: int) -> None:
    feedback = getattr(response, "prompt_feedback", None)
    if block := getattr(feedback, "block_reason", None):
        raise Refused(NAME, _name(block), getattr(feedback, "block_reason_message", None))

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise SynthError("response contained no candidates")

    reason = _name(getattr(candidates[0], "finish_reason", None))
    if reason in _REFUSAL_REASONS:
        raise Refused(NAME, reason, getattr(candidates[0], "finish_message", None))
    if reason == "MAX_TOKENS":
        raise SynthError(
            f"hit max_output_tokens ({max_tokens}); the unit is too large to synthesize"
        )
    if reason and reason not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise SynthError(f"unexpected finish_reason: {reason}")


def _name(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "name", None) or str(value)
