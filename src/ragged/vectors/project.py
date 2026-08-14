"""Embeddings -> 2D (DESIGN §6.9).

UMAP with a fixed `random_state`, falling back to PCA below 15 concepts (UMAP is
unstable at that size) or when `umap-learn` is not installed — it pulls numba, so
it is an optional extra rather than a dependency.

Risk 5 (layout instability) is only half-mitigated by the seed: a refit with new
points can still move the whole layout. `umap.transform()` on a persisted fitted
model would place new points into the existing one instead, which is the fix —
but it requires pickling the fitted model into the sidecar and a `reindex --full`
to refit deliberately, and neither is built here.
"""

from __future__ import annotations

import numpy as np

MIN_UMAP_CONCEPTS = 15


def project(
    matrix: np.ndarray, *, method: str = "umap", random_state: int = 42
) -> tuple[np.ndarray, str]:
    """Returns the `[n, 2]` coordinates and the method that actually ran."""
    if matrix.shape[0] == 0:
        return np.zeros((0, 2), np.float32), "pca"
    if method == "umap" and matrix.shape[0] >= MIN_UMAP_CONCEPTS:
        coords = _umap(matrix, random_state)
        if coords is not None:
            return coords, "umap"
    return _pca(matrix), "pca"


def _umap(matrix: np.ndarray, random_state: int) -> np.ndarray | None:
    try:
        import umap
    except ImportError:
        return None
    reducer = umap.UMAP(
        n_components=2, metric="cosine", random_state=random_state, init="spectral"
    )
    return np.asarray(reducer.fit_transform(matrix), dtype=np.float32)


def _pca(matrix: np.ndarray) -> np.ndarray:
    centered = matrix.astype(np.float32) - matrix.mean(axis=0, dtype=np.float32)
    _, _, components = np.linalg.svd(centered, full_matrices=False)
    coords = centered @ components[:2].T
    if coords.shape[1] < 2:  # one concept, or one dimension of variance
        coords = np.pad(coords, ((0, 0), (0, 2 - coords.shape[1])))
    return coords.astype(np.float32)
