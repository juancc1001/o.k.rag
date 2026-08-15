"""`.ragged/graph.json` — two graphs over one set of nodes (DESIGN §8).

The link graph is authored and sparse; the semantic graph is computed and dense.
The value is the difference: a **ghost edge** is a pair the embeddings say is
about the same thing and that nobody linked. That is the lint signal.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .. import okf
from ..okf import Concept
from .knn import top_k

GRAPH_NAME = "graph.json"
LAYOUT_NAME = "layout.json"


def build_graph(
    concepts: list[Concept],
    *,
    vector_ids: list[str],
    coords: np.ndarray,
    sims: np.ndarray,
    k: int,
    tau: float,
    today: str,
) -> dict:
    concept_ids = {c.concept_id for c in concepts}
    position = {cid: coords[i] for i, cid in enumerate(vector_ids)}

    links: list[dict] = []
    edges: set[tuple[str, str]] = set()
    # by pair, not by occurrence: one body naming one missing target twice is one
    # broken link, the same way naming a real one twice is one edge
    broken: set[tuple[str, str]] = set()
    for concept in concepts:
        targets: set[str] = set()
        for _, resolved in okf.cross_links(concept.body, f"{concept.concept_id}.md"):
            if resolved not in concept_ids:
                broken.add((concept.concept_id, resolved))
                continue
            if resolved == concept.concept_id or resolved in targets:
                continue
            targets.add(resolved)
            links.append(
                {"source": concept.concept_id, "target": resolved, "kind": "explicit"}
            )
            edges.add(_pair(concept.concept_id, resolved))

    degree: dict[str, int] = {}
    for a, b in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1

    nodes = []
    for concept in concepts:
        x, y = position.get(concept.concept_id, (0.0, 0.0))
        nodes.append(
            {
                "id": concept.concept_id,
                "title": concept.title or concept.concept_id,
                "type": concept.type,
                "status": concept.status or "stable",
                "trust": trust(concept),
                "stale": is_stale(concept, today),
                "x": round(float(x), 4),
                "y": round(float(y), 4),
                "degree": degree.get(concept.concept_id, 0),
            }
        )

    ghosts = _ghosts(vector_ids, sims, edges, k=k, tau=tau)
    return {
        "nodes": nodes,
        "links": sorted(links, key=lambda link: (link["source"], link["target"])),
        "ghosts": ghosts,
        "stats": {
            "concepts": len(nodes),
            "links": len(links),
            "ghosts": len(ghosts),
            "broken_links": len(broken),
        },
    }


def _ghosts(
    vector_ids: list[str], sims: np.ndarray, edges: set[tuple[str, str]], *, k: int, tau: float
) -> list[dict]:
    """Similar, mutually unlinked, and near enough to be one of each other's top k.

    Upper triangle only, so a pair is reported once rather than from both ends.
    """
    ghosts: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for i, neighbours in enumerate(top_k(sims, k)):
        for j in neighbours:
            low, high = (i, j) if i < j else (j, i)
            if (low, high) in seen:
                continue
            seen.add((low, high))
            similarity = float(sims[low, high])
            if similarity < tau:
                continue
            if _pair(vector_ids[low], vector_ids[high]) in edges:
                continue
            ghosts.append(
                {
                    "source": vector_ids[low],
                    "target": vector_ids[high],
                    "kind": "semantic",
                    "similarity": round(similarity, 4),
                }
            )
    return sorted(ghosts, key=lambda ghost: (ghost["source"], ghost["target"]))


def trust(concept: Concept) -> str:
    """The spec's trust tier, read off `verified`. Never written back (§4.4)."""
    if not concept.verified:
        return "unverified"
    if any(str(entry.get("by", "")).startswith("human:") for entry in concept.verified):
        return "human-reviewed"
    return "machine-confirmed"


def is_stale(concept: Concept, today: str) -> bool:
    return bool(concept.stale_after) and str(concept.stale_after) <= today


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def write_graph(sidecar_root: Path, graph: dict) -> Path:
    return _write_json(sidecar_root / GRAPH_NAME, graph)


def write_layout(sidecar_root: Path, vector_ids: list[str], coords: np.ndarray, method: str) -> Path:
    payload = {
        "version": 1,
        "method": method,
        "coords": {
            cid: [round(float(coords[i][0]), 4), round(float(coords[i][1]), 4)]
            for i, cid in enumerate(vector_ids)
        },
    }
    return _write_json(sidecar_root / LAYOUT_NAME, payload)


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path
