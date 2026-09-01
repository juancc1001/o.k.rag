"""embed -> project -> graph (DESIGN.md §6.8, §6.9, §8).

Stages 8 and 9 of the pipeline, and separable from 1–7 by design: nothing in the
build path uses embeddings, so `okrag build` never calls this and a bundle is
produced, validated and consumed without it.

Everything written here goes in `.okrag/`. The sidecar rule is the load-bearing
constraint of the design — a bundle carrying an `embedding:` key or a vector file
is no longer something you can `tar` up and read with `cat`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import okf
from ..config import Config
from ..lock import hold
from .base import EmbedError, MissingEmbedCredential
from .embed import EmbedSummary, chunk, embed_bundle, embedding_text
from .graph import GRAPH_NAME, LAYOUT_NAME, build_graph, is_stale, trust, write_graph, write_layout
from .knn import nearest, similarities, top_k
from .project import MIN_UMAP_CONCEPTS, project
from .providers import PROVIDERS, resolve
from .store import MATRIX_NAME, META_NAME, Store
from .store import load as load_store

__all__ = [
    "EmbedError",
    "EmbedSummary",
    "GRAPH_NAME",
    "IndexSummary",
    "LAYOUT_NAME",
    "MATRIX_NAME",
    "META_NAME",
    "MIN_UMAP_CONCEPTS",
    "MissingEmbedCredential",
    "PROVIDERS",
    "Store",
    "build_graph",
    "chunk",
    "embed_bundle",
    "embedding_text",
    "index",
    "is_stale",
    "load_store",
    "nearest",
    "project",
    "resolve",
    "similarities",
    "top_k",
    "trust",
    "write_graph",
    "write_layout",
]


@dataclass
class IndexSummary:
    embed: EmbedSummary
    method: str = "pca"
    """The projection that actually ran — UMAP falls back below 15 concepts."""
    stats: dict = field(default_factory=dict)
    written: list[Path] = field(default_factory=list)


def index(config: Config) -> IndexSummary:
    """Embeds, projects, writes the graph. Holds the project lock (`lock.py`,
    DESIGN.md §5) — this reads the bundle a `build` may be swapping under it,
    and two `index` runs interleaving would write one matrix beside another's
    meta."""
    with hold(config.sidecar_root, "index"):
        return _index(config)


def _index(config: Config) -> IndexSummary:
    embedded = embed_bundle(config)
    store = load_store(config.sidecar_root, embedded.provider, embedded.model)
    matrix = store.matrix if store.matrix is not None else np.zeros((0, 0), np.float32)

    coords, method = project(
        matrix,
        method=config.projection.method,
        random_state=config.projection.random_state,
    )
    graph = build_graph(
        okf.read_concepts(config.bundle_root),
        vector_ids=store.ids,
        coords=coords,
        sims=similarities(matrix),
        k=config.vectors.knn_k,
        tau=config.vectors.ghost_threshold,
        today=datetime.now(timezone.utc).date().isoformat(),
    )
    return IndexSummary(
        embed=embedded,
        method=method,
        stats=graph["stats"],
        written=[
            config.sidecar_root / MATRIX_NAME,
            config.sidecar_root / META_NAME,
            write_layout(config.sidecar_root, store.ids, coords, method),
            write_graph(config.sidecar_root, graph),
        ],
    )
