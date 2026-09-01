"""`.okrag/embeddings.npy` + `.okrag/embeddings_meta.json` (DESIGN.md §5).

Sidecar state, so every read path degrades to "nothing stored" rather than
raising: losing it costs a re-embed, never correctness.

Vectors are L2-normalized at write time, so cosine similarity is a dot product
and every consumer can assume it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MATRIX_NAME = "embeddings.npy"
META_NAME = "embeddings_meta.json"
VERSION = 1


@dataclass
class Store:
    provider: str
    model: str
    dim: int = 0
    ids: list[str] = field(default_factory=list)
    hashes: list[str] = field(default_factory=list)
    """Row → the `body_sha256` that row was embedded from."""
    matrix: np.ndarray | None = None

    def row(self, concept_id: str, body_sha256: str) -> np.ndarray | None:
        """The stored vector for this exact body, or None if it must be re-embedded."""
        previous = self.previous(concept_id)
        if previous is None or previous[1] != body_sha256:
            return None
        return previous[0]

    def previous(self, concept_id: str) -> tuple[np.ndarray, str] | None:
        """Whatever is stored for this concept and the body it was embedded from."""
        if self.matrix is None:
            return None
        try:
            at = self.ids.index(concept_id)
        except ValueError:
            return None
        return self.matrix[at], self.hashes[at]


def normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector.astype(np.float32) if norm == 0 else (vector / norm).astype(np.float32)


def load(sidecar_root: Path, provider: str, model: str) -> Store:
    """What is on disk for this provider and model, or an empty store.

    A different provider or model means different vectors of a possibly
    different width, so the stored ones are not reusable and are not mixed in.
    """
    empty = Store(provider=provider, model=model)
    meta_path = sidecar_root / META_NAME
    matrix_path = sidecar_root / MATRIX_NAME
    if not meta_path.exists() or not matrix_path.exists():
        return empty
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        if meta.get("version") != VERSION:
            return empty
        if meta.get("provider") != provider or meta.get("model") != model:
            return empty
        rows = meta.get("rows") or []
        matrix = np.load(matrix_path)
    except (OSError, ValueError, KeyError):
        return empty
    if matrix.ndim != 2 or matrix.shape[0] != len(rows):
        return empty
    return Store(
        provider=provider,
        model=model,
        dim=int(matrix.shape[1]),
        ids=[str(r.get("concept_id", "")) for r in rows],
        hashes=[str(r.get("embedded_body_sha256", "")) for r in rows],
        matrix=matrix.astype(np.float32),
    )


def save(sidecar_root: Path, store: Store) -> tuple[Path, Path]:
    sidecar_root.mkdir(parents=True, exist_ok=True)
    matrix = store.matrix if store.matrix is not None else np.zeros((0, store.dim), np.float32)
    matrix_path = sidecar_root / MATRIX_NAME
    np.save(str(matrix_path), matrix.astype(np.float32))
    payload = {
        "version": VERSION,
        "provider": store.provider,
        "model": store.model,
        "dim": store.dim,
        "rows": [
            {"concept_id": cid, "embedded_body_sha256": h}
            for cid, h in zip(store.ids, store.hashes)
        ],
    }
    meta_path = sidecar_root / META_NAME
    meta_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return matrix_path, meta_path
