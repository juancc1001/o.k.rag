"""`lint` — okflint results plus the three things it cannot see (DESIGN §10).

okflint answers conformance. Ghost edges, stale concepts and orphans are the
bundle's own health: a pair the embeddings say is about one thing and nobody
linked, a concept past its `stale_after`, and a concept nothing points at.

Entirely offline, and read-only. Ghost edges come from `.ragged/graph.json` as
`ragged index` left it, so no credential and no model call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import okf
from ..config import Config
from ..vectors import GRAPH_NAME, is_stale


@dataclass
class LintReport:
    conformant: bool = False
    concepts: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    okflint_exit: int | None = None
    okflint_note: str = ""
    """Why okflint did not run, when it didn't."""
    ghosts: list[dict] = field(default_factory=list)
    ghosts_note: str = ""
    """Why there are no ghost edges to report, when there are none to read."""
    stale: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    """Concepts no other concept links to. Reachable only through the indexes."""


def lint(config: Config) -> LintReport:
    report = okf.validate(config.bundle_root)
    concepts = okf.read_concepts(config.bundle_root)
    today = datetime.now(timezone.utc).date().isoformat()
    ghosts, note = _ghosts(config)
    return LintReport(
        conformant=report.ok,
        concepts=report.concepts,
        errors=[str(finding) for finding in report.errors],
        warnings=[str(finding) for finding in report.warnings],
        okflint_exit=report.okflint_exit,
        okflint_note=report.okflint_note,
        ghosts=ghosts,
        ghosts_note=note,
        stale=[c.concept_id for c in concepts if is_stale(c, today)],
        orphans=_orphans(concepts),
    )


def _ghosts(config: Config) -> tuple[list[dict], str]:
    path = config.sidecar_root / GRAPH_NAME
    if not path.exists():
        return [], f"no {GRAPH_NAME}; run `ragged index` to compute ghost edges"
    try:
        graph = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # sidecar state: a graph that does not read costs a re-index, not a lint
        return [], f"{GRAPH_NAME} could not be read; re-run `ragged index`"
    return list(graph.get("ghosts") or []), ""


def _orphans(concepts: list[okf.Concept]) -> list[str]:
    linked: set[str] = set()
    for concept in concepts:
        for _, target in okf.cross_links(concept.body, f"{concept.concept_id}.md"):
            if target != concept.concept_id:
                linked.add(target)
    return [c.concept_id for c in concepts if c.concept_id not in linked]
