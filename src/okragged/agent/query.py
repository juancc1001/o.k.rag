"""The query half of the agent contract: `search` and `read_concept` (DESIGN.md §10).

`search` returns pointers — id, title, description, score — never a body. The
agent decides what to read (progressive disclosure, DESIGN.md §7), not chunk
retrieval.

Two ranking paths, one result shape. Concept vectors are computed at build
time, but embedding a *query* is a read-time model call needing a credential
— so the embedding path is an upgrade: without stored vectors or without a
credential, `search` falls back to traversing what the bundle already says
about itself (DESIGN.md §6.8).

**A concept the stored vectors don't cover is ranked anyway — this isn't a
nicety.** Vectors come from `okragged index`, a separate command, so any
concept written since the last run has no row in the matrix, and kNN can't
rank a candidate that isn't in it. Silently answering from only the covered
concepts and returning an empty `note` would be worse than an error: the
caller would conclude the bundle has no answer, or answer from the wrong
neighbours. So uncovered concepts are ranked by traversal instead and merged
in, and `note` says how many and what fixes it.

**The two rankings are merged without being compared.** A cosine similarity
and a normalized term-overlap score are different quantities, so the merge
alternates the two lists by rank rather than sorting them together as if one
number meant the same thing on both sides. `note` says the scores aren't
comparable, and the traversal side only contributes concepts the query
actually matched — an unrelated new concept isn't promoted into every result
just for having been written recently.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path

import numpy as np

from .. import okf
from ..config import Config
from ..okf import Concept
from ..vectors import EmbedError, load_store, nearest
from ..vectors.providers import resolve

EMBEDDING = "embeddings"
TRAVERSAL = "index-traversal"
MERGED = "embeddings+index-traversal"
"""Both rankings, alternated by rank. Only when the stored vectors leave a
concept out *and* the query matches it: there is nothing to merge otherwise."""

TITLE_WEIGHT = 3.0
DESCRIPTION_WEIGHT = 2.0
BODY_WEIGHT = 1.0
NEIGHBOUR_WEIGHT = 0.25
"""What one hop along an authored link is worth, as a fraction of the hit it came
from. A concept the bundle links to a hit is about the same thing at second hand."""

PREFIX_MIN = 4
"""Shortest term allowed to match by prefix, so `delito` finds `delitos` without
`ley` finding `leyenda`."""


@dataclass(frozen=True)
class Hit:
    concept_id: str
    title: str
    description: str
    score: float


@dataclass
class SearchResult:
    path: str
    """Which ranking answered: `embeddings`, `index-traversal`, or
    `embeddings+index-traversal` when both did and the scores are per-group."""
    hits: list[Hit] = field(default_factory=list)
    note: str = ""
    """What the caller would otherwise not know: why the embedding path did not
    answer, or which concepts it could not rank. Empty only when the ranking was
    complete."""


def search(config: Config, query: str, k: int = 8) -> SearchResult:
    concepts = okf.read_concepts(config.bundle_root)
    hits, vectored, why = _by_embedding(config, query, k, concepts)
    if hits is None:
        return SearchResult(path=TRAVERSAL, hits=_by_traversal(query, k, concepts), note=why)

    unvectored = [c.concept_id for c in concepts if c.concept_id not in vectored]
    if not unvectored:
        return SearchResult(path=EMBEDDING, hits=hits)
    if not vectored:
        # a matrix whose every row names a concept the bundle no longer has: the
        # embedding path has nothing to rank, so say that rather than merge with it
        return SearchResult(
            path=TRAVERSAL,
            hits=_by_traversal(query, k, concepts),
            note="the stored vectors name no concept in this bundle; run `okragged index`",
        )

    # ranked over the whole bundle and then narrowed, so the link boosts and the
    # normalization still see every concept -- only the *hits* are narrowed to the
    # ones kNN could not have returned
    outside = set(unvectored)
    extra = [hit for hit in _by_traversal(query, k, concepts) if hit.concept_id in outside]
    note = _incomplete(len(unvectored))
    if not extra:
        return SearchResult(path=EMBEDDING, hits=hits, note=note)
    return SearchResult(path=MERGED, hits=_alternating(hits, extra, k), note=note)


def _incomplete(count: int) -> str:
    return (
        f"{count} concept(s) have no stored vector and were ranked by index traversal "
        "instead, so their scores are not comparable with the embedding scores; run "
        "`okragged index` to embed them"
    )


def _alternating(vectored: list[Hit], unvectored: list[Hit], k: int) -> list[Hit]:
    """One from each list in turn, then whatever is left of the longer one.

    Not sorted together: the two scores are different quantities. Alternating
    gives a concept without a vector the position its own ranking earned it,
    which is the only ranking it can have until `okragged index` runs.
    """
    merged = [
        hit
        for pair in zip_longest(vectored, unvectored)
        for hit in pair
        if hit is not None
    ]
    return merged[:k]


def read_concept(config: Config, concept_id: str) -> str:
    """The raw file, frontmatter included. Reserved files (`index.md`) too."""
    return _resolve(config.bundle_root, concept_id).read_text(encoding="utf-8-sig")


def _resolve(bundle_root: Path, concept_id: str) -> Path:
    relative = concept_id.strip().replace("\\", "/").lstrip("/").removesuffix(".md")
    path = bundle_root / f"{relative}.md"
    inside = path.resolve().is_relative_to(bundle_root.resolve())
    if not relative or not inside or not path.is_file():
        raise LookupError(f"no concept {concept_id!r} in the bundle")
    return path


def _by_embedding(
    config: Config, query: str, k: int, concepts: list[Concept]
) -> tuple[list[Hit] | None, set[str], str]:
    """kNN over the stored matrix, the concepts it covers, and why it could not run.

    The covered set is returned rather than derived by the caller because it is
    what the matrix actually holds: a concept absent from it could not have been
    a candidate, however well it matches.
    """
    try:
        impl = resolve(config.vectors.provider)
    except ValueError as exc:
        return None, set(), str(exc)
    model = config.vectors.model or impl.DEFAULT_MODEL
    store = load_store(config.sidecar_root, config.vectors.provider, model)
    if store.matrix is None or not store.ids:
        return None, set(), "no stored embeddings for this provider; run `okragged index`"
    if not impl.credential_present():
        return None, set(), (
            f"no credential for embedding provider {config.vectors.provider!r}, "
            "so the query cannot be embedded"
        )
    try:
        raw = np.asarray(
            impl.embed([query], model=model, dim=config.vectors.dim), dtype=np.float32
        )
    except EmbedError as exc:
        return None, set(), str(exc)
    if raw.ndim != 2 or not raw.size or raw.shape[1] != store.matrix.shape[1]:
        return None, set(), "the stored vectors are a different width; re-run `okragged index`"

    norm = float(np.linalg.norm(raw[0]))
    vector = raw[0] if norm == 0 else raw[0] / norm
    by_id = {concept.concept_id: concept for concept in concepts}
    covered = {cid for cid in store.ids if cid in by_id}
    hits = [
        _hit(by_id[store.ids[row]], score)
        for row, score in nearest(store.matrix, vector.astype(np.float32), k)
        if store.ids[row] in by_id
    ]
    return hits, covered, ""


def _by_traversal(query: str, k: int, concepts: list[Concept]) -> list[Hit]:
    """Rank over what the bundle says about itself.

    Every index entry is its target's own `title` and `description` verbatim
    (DESIGN.md §6.6), so ranking those is ranking the indexes; the body is the
    same match at lower weight, and the authored links carry a hit to its
    neighbours.
    """
    terms = list(dict.fromkeys(_terms(query)))
    if not terms:
        return []
    base = {concept.concept_id: _score(concept, terms) for concept in concepts}
    if not any(base.values()):
        return []

    total = dict(base)
    for concept in concepts:
        # one hop per *pair*, not per link: a body that names the same target
        # three times asserts one relationship, and counting it three times
        # would let repetition outrank relevance
        for target in _targets(concept, base):
            total[target] += NEIGHBOUR_WEIGHT * base[concept.concept_id]
            total[concept.concept_id] += NEIGHBOUR_WEIGHT * base[target]

    best = max(total.values())
    ranked = sorted(total.items(), key=lambda item: (-item[1], item[0]))
    by_id = {concept.concept_id: concept for concept in concepts}
    return [
        _hit(by_id[concept_id], score / best)
        for concept_id, score in ranked[:k]
        if score > 0
    ]


def _targets(concept: Concept, known: dict[str, float]) -> list[str]:
    """The distinct concepts this body links to, itself and strangers excluded."""
    seen: dict[str, None] = {}
    for _, target in okf.cross_links(concept.body, f"{concept.concept_id}.md"):
        if target in known and target != concept.concept_id:
            seen[target] = None
    return list(seen)


def _hit(concept: Concept, score: float) -> Hit:
    return Hit(
        concept_id=concept.concept_id,
        title=concept.title or concept.concept_id,
        description=concept.description or "",
        score=round(float(score), 4),
    )


def _score(concept: Concept, terms: list[str]) -> float:
    fields = (
        (concept.title or concept.concept_id, TITLE_WEIGHT),
        (concept.description or "", DESCRIPTION_WEIGHT),
        (concept.body, BODY_WEIGHT),
    )
    total = 0.0
    for text, weight in fields:
        tokens = _terms(text)
        total += weight * sum(1 for term in terms if _present(term, tokens))
    return total


def _present(term: str, tokens: list[str]) -> bool:
    if term in tokens:
        return True
    return len(term) >= PREFIX_MIN and any(token.startswith(term) for token in tokens)


def _terms(text: str) -> list[str]:
    """Accent-folded lowercase words, so `informaticos` matches `informáticos`."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return [token for token in re.split(r"[^a-z0-9]+", folded) if len(token) > 1]
