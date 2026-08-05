from __future__ import annotations

import re
import unicodedata
from typing import Any

import yaml
from pydantic import BaseModel, Field

DELIM = "---"

# Frontmatter key order: required, then recommended, then provenance/trust/lifecycle,
# then producer extensions. Purely cosmetic — the spec imposes no ordering.
KEY_ORDER = [
    "type",
    "title",
    "description",
    "resource",
    "tags",
    "sources",
    "usage_window",
    "generated",
    "verified",
    "status",
    "stale_after",
]


class Source(BaseModel):
    """An entry in `sources`. `resource` is the only required field."""

    resource: str
    id: str | None = None
    title: str | None = None
    author: str | None = None
    usage_count: int | None = None
    last_modified: str | None = None


class Concept(BaseModel):
    concept_id: str
    """Path within the bundle, minus the .md suffix."""

    type: str
    body: str
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    generated: dict[str, str] | None = None
    verified: list[dict[str, str]] = Field(default_factory=list)
    status: str | None = None
    stale_after: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    """Unknown/producer-defined keys. Preserved verbatim on round-trip."""

    def frontmatter(self) -> dict[str, Any]:
        known = {
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "resource": self.resource,
            "tags": self.tags or None,
            "sources": [s.model_dump(exclude_none=True) for s in self.sources] or None,
            "generated": self.generated,
            "verified": self.verified or None,
            "status": self.status,
            "stale_after": self.stale_after,
        }
        out = {k: v for k, v in known.items() if v is not None}
        out.update(self.extra)
        return out


def render(concept: Concept) -> str:
    fm = yaml.safe_dump(
        concept.frontmatter(),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    body = concept.body.strip()
    return f"{DELIM}\n{fm}\n{DELIM}\n\n{body}\n"


def parse(text: str, concept_id: str) -> Concept:
    """Inverse of `render`. Unknown frontmatter keys land in `extra`."""
    fm, body = split(text)
    if fm is None:
        raise ValueError(f"{concept_id}: no YAML frontmatter (E1)")
    data = yaml.safe_load(fm) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{concept_id}: frontmatter is not a mapping (E1)")
    if not str(data.get("type") or "").strip():
        raise ValueError(f"{concept_id}: missing or empty `type` (E2)")

    verified = data.get("verified")
    if isinstance(verified, dict):  # spec: a bare mapping is a one-element list
        verified = [verified]

    return Concept(
        concept_id=concept_id,
        type=data["type"],
        body=body,
        title=data.get("title"),
        description=data.get("description"),
        resource=data.get("resource"),
        tags=data.get("tags") or [],
        sources=[Source(**s) for s in (data.get("sources") or [])],
        generated=data.get("generated"),
        verified=verified or [],
        status=data.get("status"),
        stale_after=data.get("stale_after"),
        extra={k: v for k, v in data.items() if k not in KEY_ORDER},
    )


def split(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_yaml, body). Frontmatter is None when absent."""
    stripped = text.lstrip("﻿")
    if not stripped.startswith(DELIM):
        return None, text
    lines = stripped.splitlines()
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == DELIM:
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :]).strip()
    return None, text


def slugify(value: str) -> str:
    """Filesystem-safe concept id segment. Preserves `/` so nesting survives."""
    value = value.strip().lower().replace("\\", "/")
    # fold accents to their base letter before stripping, or `informáticos`
    # becomes `informticos` instead of `informaticos`
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9/\-_. ]+", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    segments = [seg.strip("-.") for seg in value.split("/")]
    return "/".join(seg for seg in segments if seg)
