"""The query half of the agent contract: `search` and `read_concept` (DESIGN §10).

`search` returns pointers — id, title, description, score — and never a body. The
agent decides what to read, which is the progressive-disclosure path of §7 rather
than chunk retrieval.

Two ranking paths, one result shape. Concept vectors are computed at build time,
but embedding a *query* is a read-time model call and needs a credential, and the
M4 exit criterion is a session that has none (§13). So the embedding path is an
upgrade: without stored vectors or without a credential, `search` falls back to
traversing what the bundle already says about itself (§6.8).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import okf
from ..config import Config
from ..okf import Concept
from ..vectors import EmbedError, load_store, nearest
from ..vectors.providers import resolve

EMBEDDING = "embeddings"
TRAVERSAL = "index-traversal"

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
    """Which ranking answered: `embeddings` or `index-traversal`."""
    hits: list[Hit] = field(default_factory=list)
    note: str = ""
    """Why the embedding path did not answer, when it didn't."""


def search(config: Config, query: str, k: int = 8) -> SearchResult:
    concepts = okf.read_concepts(config.bundle_root)
    hits, why = _by_embedding(config, query, k, concepts)
    if hits is not None:
        return SearchResult(path=EMBEDDING, hits=hits)
    return SearchResult(path=TRAVERSAL, hits=_by_traversal(query, k, concepts), note=why)


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
) -> tuple[list[Hit] | None, str]:
    """kNN over the stored matrix, or `None` and the reason it could not run."""
    try:
        impl = resolve(config.vectors.provider)
    except ValueError as exc:
        return None, str(exc)
    model = config.vectors.model or impl.DEFAULT_MODEL
    store = load_store(config.sidecar_root, config.vectors.provider, model)
    if store.matrix is None or not store.ids:
        return None, "no stored embeddings for this provider; run `ragged index`"
    if not impl.credential_present():
        return None, (
            f"no credential for embedding provider {config.vectors.provider!r}, "
            "so the query cannot be embedded"
        )
    try:
        raw = np.asarray(
            impl.embed([query], model=model, dim=config.vectors.dim), dtype=np.float32
        )
    except EmbedError as exc:
        return None, str(exc)
    if raw.ndim != 2 or not raw.size or raw.shape[1] != store.matrix.shape[1]:
        return None, "the stored vectors are a different width; re-run `ragged index`"

    norm = float(np.linalg.norm(raw[0]))
    vector = raw[0] if norm == 0 else raw[0] / norm
    by_id = {concept.concept_id: concept for concept in concepts}
    hits = [
        _hit(by_id[store.ids[row]], score)
        for row, score in nearest(store.matrix, vector.astype(np.float32), k)
        if store.ids[row] in by_id
    ]
    return hits, ""


def _by_traversal(query: str, k: int, concepts: list[Concept]) -> list[Hit]:
    """Rank over what the bundle says about itself.

    Every index entry is its target's own `title` and `description` verbatim
    (§6.6), so ranking those is ranking the indexes; the body is the same match
    at lower weight, and the authored links carry a hit to its neighbours.
    """
    terms = list(dict.fromkeys(_terms(query)))
    if not terms:
        return []
    base = {concept.concept_id: _score(concept, terms) for concept in concepts}
    if not any(base.values()):
        return []

    total = dict(base)
    for concept in concepts:
        for _, target in okf.cross_links(concept.body, f"{concept.concept_id}.md"):
            if target not in base or target == concept.concept_id:
                continue
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
