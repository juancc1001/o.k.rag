"""`check_citations` (DESIGN.md §13 M11, item 2): is an answer's claim to rest on
the bundle actually true of the bundle?

Given the concept ids an answer cites and the spans it quotes, check that each
id names a concept and that each quote occurs in the concept it is attributed
to. **No model call and no network** — the `prune_links` pattern one level up:
a check that runs offline is testable, and a prompt asking a model to cite
honestly is not.

Matching is exact after normalizing both sides the same way, so formatting is
forgiven and wording is not: a quote that differs in line breaks, case, accents
or markdown emphasis still matches, and a quote that paraphrases does not.
Punctuation is kept. The search space is the concept's title, description and
body — what it asserts — never the raw YAML around them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .. import okf
from ..config import Config
from ..okf.inventory import RESERVED
from .query import resolve_concept_path

NO_SUCH_CONCEPT = "no such concept"
NOT_A_CONCEPT = "not a concept"
QUOTE_NOT_FOUND = "quote not found in the cited concept"
QUOTE_TOO_SHORT = "quote too short"

MIN_QUOTE_WORDS = 3
"""A shorter quote occurs almost anywhere, so finding it proves nothing."""

LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


@dataclass(frozen=True)
class Rejection:
    concept_id: str
    quote: str | None
    reason: str


@dataclass
class CitationReport:
    ok: bool
    checked: int
    rejected: list[Rejection] = field(default_factory=list)


def check_citations(config: Config, citations: list[dict[str, Any]]) -> CitationReport:
    """Each citation is `{concept_id, quote}`; with no quote, only existence is checked."""
    rejected = []
    for citation in citations:
        concept_id = str(citation.get("concept_id") or "")
        quote = citation.get("quote")
        reason = _reason(config, concept_id, quote)
        if reason:
            rejected.append(Rejection(concept_id=concept_id, quote=quote, reason=reason))
    return CitationReport(ok=not rejected, checked=len(citations), rejected=rejected)


def _reason(config: Config, concept_id: str, quote: str | None) -> str:
    try:
        path = resolve_concept_path(config.bundle_root, concept_id)
    except LookupError:
        return NO_SUCH_CONCEPT
    if path.name in RESERVED:
        return NOT_A_CONCEPT
    try:
        concept = okf.parse(path.read_text(encoding="utf-8-sig"), concept_id)
    except ValueError:
        return NOT_A_CONCEPT
    if quote is None:
        return ""
    wanted = _normalized(quote)
    if len(wanted.split()) < MIN_QUOTE_WORDS:
        return QUOTE_TOO_SHORT
    text = "\n".join((concept.title or "", concept.description or "", concept.body))
    return "" if wanted in _normalized(text) else QUOTE_NOT_FOUND


def _normalized(text: str) -> str:
    """Formatting forgiven, wording not: links read as their label, emphasis and
    code marks dropped, case and accents folded, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text)
    text = LINK.sub(r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())
