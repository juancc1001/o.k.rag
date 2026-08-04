"""Text -> one OKF concept, via whichever provider is configured.

Provider modules stay thin: they own authentication, the request shape, and
decline detection. Everything shared — prompt assembly, schema, JSON parsing,
footnote repair — lives here so the two paths cannot drift.
"""

from __future__ import annotations

import json

from .base import (
    CONCEPT_SCHEMA,
    MissingCredential,
    ProviderHTTPError,
    ProviderResponse,
    Refused,
    SynthError,
    SynthResult,
    body_defines,
    ensure_footnote_defs,
)
from .prompt import system_prompt, user_prompt
from .providers import PROVIDERS, resolve

__all__ = [
    "CONCEPT_SCHEMA",
    "MissingCredential",
    "ProviderHTTPError",
    "ProviderResponse",
    "Refused",
    "SynthError",
    "SynthResult",
    "PROVIDERS",
    "resolve",
    "body_defines",
    "ensure_footnote_defs",
    "synthesize",
]


def synthesize(
    *,
    text: str,
    source_id: str,
    source_uri: str,
    source_title: str,
    allowed_types: list[str],
    provider: str = "anthropic",
    model: str | None = None,
    effort: str = "high",
    max_tokens: int = 16000,
) -> SynthResult:
    impl = resolve(provider)
    response = impl.call(
        system=system_prompt(allowed_types),
        user=user_prompt(source_id, source_uri, source_title, text),
        schema=CONCEPT_SCHEMA,
        model=model or impl.DEFAULT_MODEL,
        effort=effort,
        max_tokens=max_tokens,
    )

    try:
        data = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise SynthError(f"{provider} returned unparseable JSON: {exc}") from exc

    missing = [k for k in ("type", "title", "description", "body_markdown") if not data.get(k)]
    if missing:
        raise SynthError(f"{provider} omitted required field(s): {', '.join(missing)}")

    return SynthResult(
        type=str(data["type"]).strip(),
        title=str(data["title"]).strip(),
        description=str(data["description"]).strip(),
        body_markdown=ensure_footnote_defs(
            str(data["body_markdown"]).strip(), source_id, source_title
        ),
        tags=[str(t).strip() for t in data.get("tags") or [] if str(t).strip()],
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
    )
