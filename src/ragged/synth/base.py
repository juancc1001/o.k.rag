from __future__ import annotations

import re
from dataclasses import dataclass

# The concept schema. Both providers enforce it natively: Anthropic via
# output_config.format json_schema, Gemini via response_json_schema.
CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "body_markdown": {"type": "string"},
    },
    "required": ["type", "title", "description", "body_markdown"],
    "additionalProperties": False,
}

FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\]")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:", re.MULTILINE)


class SynthError(RuntimeError):
    pass


class MissingCredential(SynthError):
    pass


class Refused(SynthError):
    """Safety classifiers declined. Not a transport failure — retrying won't help."""

    def __init__(self, provider: str, category: str | None, explanation: str | None) -> None:
        detail = f" {explanation}" if explanation else ""
        super().__init__(f"{provider} declined ({category or 'unspecified'}):{detail}".rstrip())
        self.category = category
        self.explanation = explanation


@dataclass
class ProviderResponse:
    """What a provider returns: the raw JSON payload plus token accounting."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class SynthResult:
    type: str
    title: str
    description: str
    body_markdown: str
    tags: list[str]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


def ensure_footnote_defs(body: str, source_id: str, source_title: str) -> str:
    """A referenced footnote with no definition renders as literal text. Append it."""
    defined = set(FOOTNOTE_DEF_RE.findall(body))
    referenced = {r for r in FOOTNOTE_REF_RE.findall(body) if not body_defines(body, r)}
    missing = sorted(referenced - defined)
    if not missing:
        return body
    lines = [f"[^{ref}]: {source_title if ref == source_id else ref}" for ref in missing]
    return body.rstrip() + "\n\n" + "\n".join(lines)


def body_defines(body: str, ref: str) -> bool:
    return f"[^{ref}]:" in body
