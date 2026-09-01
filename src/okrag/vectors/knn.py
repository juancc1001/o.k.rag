"""Brute-force cosine kNN over the L2-normalized matrix (DESIGN.md §6.9).

`n x dim` float32 is 1.5 MB at 1000 concepts; NumPy does this in milliseconds and
the code is twenty lines. hnswlib is worth it only past ~100k concepts.
"""

from __future__ import annotations

import numpy as np


def similarities(matrix: np.ndarray) -> np.ndarray:
    """Full cosine matrix. Rows are normalized, so it is a dot product."""
    if matrix.shape[0] == 0:
        return np.zeros((0, 0), np.float32)
    sims = (matrix @ matrix.T).astype(np.float32)
    np.fill_diagonal(sims, -np.inf)
    return sims


def nearest(matrix: np.ndarray, query: np.ndarray, k: int) -> list[tuple[int, float]]:
    """The k rows nearest one query vector, as `(row, similarity)`, nearest first.

    Both sides are L2-normalized, so this is the same dot product as `similarities`
    with one side held fixed — which is what `search` asks when it has a credential
    to embed the query with (DESIGN.md §10, the agent contract).
    """
    if matrix.shape[0] == 0:
        return []
    sims = (matrix @ query).astype(np.float32)
    order = np.argsort(-sims)[: max(k, 0)]
    return [(int(row), float(sims[row])) for row in order]


def top_k(sims: np.ndarray, k: int) -> list[list[int]]:
    """Each row's k nearest neighbours, nearest first."""
    n = sims.shape[0]
    width = min(k, max(n - 1, 0))
    if width == 0:
        return [[] for _ in range(n)]
    partitioned = np.argpartition(-sims, width - 1, axis=1)[:, :width]
    return [
        sorted(row.tolist(), key=lambda j, i=i: -float(sims[i, j]))
        for i, row in enumerate(partitioned)
    ]
