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

# The outline schema for the planning pass. Sections carry a verbatim search key
# rather than offsets, which models do not produce reliably.
OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "overview": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start_marker": {"type": "string"},
                },
                "required": ["title", "start_marker"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["type", "title", "description", "overview", "sections"],
    "additionalProperties": False,
}

# §4.3 granularity bounds, in words of source text.
MIN_SECTION_WORDS = 150
MAX_CONCEPT_WORDS = 1200

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


class ProviderHTTPError(SynthError):
    """Anything the provider answered that was not a 2xx, plus transport failures.

    A SynthError so the pipeline records it against the file and keeps going:
    a 429 on file 3 must not discard the concepts already written for 1 and 2.
    """

    def __init__(self, provider: str, status: int | None, message: str) -> None:
        super().__init__(f"{provider} HTTP {status or 'no status'}: {_one_line(message)}")
        self.status = status


def _one_line(message: str, limit: int = 300) -> str:
    """Provider errors arrive multi-line; the build summary prints one line per file."""
    collapsed = " ".join(str(message).split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


@dataclass
class ProviderResponse:
    """What a provider returns: the raw JSON payload plus token accounting."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class SectionContext:
    """Where a section sits in its parent document, for the per-section prompt."""

    document_title: str
    document_overview: str
    section_title: str
    index: int
    total: int

    @property
    def position(self) -> str:
        return f"section {self.index} of {self.total}"


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
