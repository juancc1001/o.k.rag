"""Text -> one OKF concept, via whichever provider is configured.

Provider modules stay thin: they own authentication, the request shape, and
decline detection. Everything shared — prompt assembly, schema, JSON parsing,
footnote repair — lives here so the two paths cannot drift.
"""

from __future__ import annotations

import json

from .base import (
    CONCEPT_SCHEMA,
    MAX_CONCEPT_WORDS,
    MIN_SECTION_WORDS,
    OUTLINE_SCHEMA,
    WEAVE_SCHEMA,
    MissingCredential,
    ProviderHTTPError,
    ProviderResponse,
    Refused,
    SectionContext,
    SynthError,
    SynthResult,
    WeaveResult,
    anchor_footnote_defs,
    body_defines,
    ensure_footnote_defs,
)
from .prompt import section_user_prompt, system_prompt, user_prompt
from .providers import PROVIDERS, resolve
from .segment import Outline, Section, cut, needs_segmenting, plan
from .weave import prune_links, weave

__all__ = [
    "CONCEPT_SCHEMA",
    "MAX_CONCEPT_WORDS",
    "MIN_SECTION_WORDS",
    "OUTLINE_SCHEMA",
    "WEAVE_SCHEMA",
    "MissingCredential",
    "Outline",
    "ProviderHTTPError",
    "ProviderResponse",
    "Refused",
    "Section",
    "SectionContext",
    "SynthError",
    "SynthResult",
    "WeaveResult",
    "PROVIDERS",
    "resolve",
    "anchor_footnote_defs",
    "body_defines",
    "cut",
    "ensure_footnote_defs",
    "needs_segmenting",
    "plan",
    "prune_links",
    "synthesize",
    "weave",
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
    section: SectionContext | None = None,
    index: list[tuple[str, str]] | None = None,
) -> SynthResult:
    impl = resolve(provider)
    if section is None:
        user = user_prompt(source_id, source_uri, source_title, text)
    else:
        user = section_user_prompt(
            source_id,
            source_uri,
            source_title,
            text,
            document_title=section.document_title,
            document_overview=section.document_overview,
            section_title=section.section_title,
            position=section.position,
        )
    response = impl.call(
        # byte-identical across whole-document and per-section calls, so the
        # cached prefix survives segmentation
        system=system_prompt(allowed_types, index),
        user=user,
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
            anchor_footnote_defs(str(data["body_markdown"]).strip()),
            source_id,
            source_title,
        ),
        tags=[str(t).strip() for t in data.get("tags") or [] if str(t).strip()],
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
        candidate_links=_known_links(data.get("candidate_links"), index),
    )


def _known_links(proposed: object, index: list[tuple[str, str]] | None) -> list[str]:
    """Ids that exist. A hint pointing at nothing would only waste a weave prompt."""
    known = {cid for cid, _ in index or []}
    ids = (str(x).strip().strip("/").removesuffix(".md") for x in proposed or [])
    return [cid for cid in ids if cid in known]
