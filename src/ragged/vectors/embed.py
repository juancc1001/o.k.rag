"""One vector per concept, over `title + description + body` (DESIGN §6.8).

Nothing in the build path uses this. Embeddings exist for the graph — layout and
the missing-link lint — and they live in `.ragged/`, never in the bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .. import okf
from ..config import Config
from ..ledger import body_hash
from ..okf import Concept
from .base import EmbedError, MissingEmbedCredential
from .providers import resolve
from .store import Store, load, normalize, save


@dataclass
class EmbedSummary:
    embedded: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    """Body unchanged since the recorded `embedded_body_sha256`; no call made."""
    errors: dict[str, str] = field(default_factory=dict)
    """concept_id -> why. One concept failing never costs the others."""
    dropped: list[str] = field(default_factory=list)
    """Rows whose concept is no longer in the bundle."""
    provider: str = ""
    model: str = ""
    dim: int = 0


def embed_bundle(config: Config) -> EmbedSummary:
    impl = resolve(config.vectors.provider)
    model = config.vectors.model or impl.DEFAULT_MODEL
    if not impl.credential_present():
        raise MissingEmbedCredential(
            f"no credential for embedding provider {config.vectors.provider!r}; "
            f"set one of {', '.join(impl.ENV_VARS)} in .env or the environment"
        )

    concepts = okf.read_concepts(config.bundle_root)
    stored = load(config.sidecar_root, config.vectors.provider, model)
    summary = EmbedSummary(provider=config.vectors.provider, model=model)

    ids: list[str] = []
    hashes: list[str] = []
    rows: list[np.ndarray] = []
    for concept in concepts:
        digest = body_hash(concept.body)
        vector = stored.row(concept.concept_id, digest)
        if vector is not None:
            summary.reused.append(concept.concept_id)
        else:
            try:
                vector = _embed_one(impl, concept, model=model, dim=config.vectors.dim)
            except EmbedError as exc:
                summary.errors[concept.concept_id] = str(exc)
                # an out-of-date vector still positions the node, and the hash it
                # is recorded against is what makes the next run retry it
                stale = stored.previous(concept.concept_id)
                if stale is not None:
                    ids.append(concept.concept_id)
                    hashes.append(stale[1])
                    rows.append(stale[0])
                continue
            summary.embedded.append(concept.concept_id)
        ids.append(concept.concept_id)
        hashes.append(digest)
        rows.append(vector)

    summary.dropped = [cid for cid in stored.ids if cid not in set(ids)]
    matrix = np.vstack(rows).astype(np.float32) if rows else None
    summary.dim = int(matrix.shape[1]) if matrix is not None else 0
    save(
        config.sidecar_root,
        Store(
            provider=config.vectors.provider,
            model=model,
            dim=summary.dim,
            ids=ids,
            hashes=hashes,
            matrix=matrix,
        ),
    )
    return summary


def _embed_one(impl, concept: Concept, *, model: str, dim: int) -> np.ndarray:
    """Mean-pool the chunks, keep only the concept vector (§6.8).

    Chunk vectors are deliberately not stored: the retrieval path is progressive
    disclosure, not chunk RAG.
    """
    chunks = chunk(embedding_text(concept), impl.WINDOW_CHARS)
    vectors = np.asarray(impl.embed(chunks, model=model, dim=dim), dtype=np.float32)
    if vectors.ndim != 2 or not vectors.size:
        raise EmbedError(f"{impl.NAME} returned no usable vector")
    return normalize(vectors.mean(axis=0))


def embedding_text(concept: Concept) -> str:
    parts = [concept.title or concept.concept_id, concept.description or "", concept.body]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def chunk(text: str, limit: int) -> list[str]:
    """Whitespace-delimited pieces of at most `limit` characters."""
    text = text.strip()
    if len(text) <= limit:
        return [text or ""]
    chunks: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > limit:  # one unbroken token longer than the window
            if current:
                chunks.append(current)
                current = ""
            chunks.append(word[:limit])
            word = word[limit:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= limit:
            current = f"{current} {word}"
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks
