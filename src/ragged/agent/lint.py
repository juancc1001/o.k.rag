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

GHOST_LIMIT = 20
"""Ghost edges grow with the square of the corpus, and this is a tool result an
agent pays for by the token. The full set is in `.ragged/graph.json`, which is
where the graph view reads it from; `ghosts_total` says what was left out."""


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
    """At most `GHOST_LIMIT`, highest similarity first."""
    ghosts_total: int = 0
    """How many the graph actually holds, truncated or not."""
    ghosts_note: str = ""
    """Anything that qualifies the ghost edges: none to read, a truncated list,
    or a graph that no longer describes this bundle."""
    stale: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    """Concepts no other concept links to. Reachable only through the indexes."""


def lint(config: Config) -> LintReport:
    report = okf.validate(config.bundle_root)
    concepts = okf.read_concepts(config.bundle_root)
    today = datetime.now(timezone.utc).date().isoformat()
    ghosts, total, note = _ghosts(config, {c.concept_id for c in concepts})
    return LintReport(
        conformant=report.ok,
        concepts=report.concepts,
        errors=[str(finding) for finding in report.errors],
        warnings=[str(finding) for finding in report.warnings],
        okflint_exit=report.okflint_exit,
        okflint_note=report.okflint_note,
        ghosts=ghosts,
        ghosts_total=total,
        ghosts_note=note,
        stale=[c.concept_id for c in concepts if is_stale(c, today)],
        orphans=_orphans(concepts),
    )


def _ghosts(config: Config, concept_ids: set[str]) -> tuple[list[dict], int, str]:
    path = config.sidecar_root / GRAPH_NAME
    if not path.exists():
        return [], 0, f"no {GRAPH_NAME}; run `ragged index` to compute ghost edges"
    try:
        graph = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # sidecar state: a graph that does not read costs a re-index, not a lint
        return [], 0, f"{GRAPH_NAME} could not be read; re-run `ragged index`"

    ghosts = sorted(
        graph.get("ghosts") or [], key=lambda g: -float(g.get("similarity") or 0)
    )
    notes = [n for n in (_drift(graph, concept_ids), _truncation(len(ghosts))) if n]
    return ghosts[:GHOST_LIMIT], len(ghosts), "; ".join(notes)


def _drift(graph: dict, concept_ids: set[str]) -> str:
    """Whether the graph still describes this bundle.

    Concept ids derive from model-written titles, so a rebuild renames the tree
    as a matter of course (§5) — which makes a `graph.json` from before it not
    merely incomplete but *about different concepts*. Reporting the ghosts
    anyway would attribute missing links to ids the bundle no longer has.
    """
    graphed = {str(node.get("id") or "") for node in (graph.get("nodes") or [])}
    if not graphed or graphed == concept_ids:
        return ""
    gone, missing = len(graphed - concept_ids), len(concept_ids - graphed)
    return (
        f"{GRAPH_NAME} is out of date: {gone} concept(s) it names are no longer in "
        f"the bundle and {missing} are missing from it; re-run `ragged index`"
    )


def _truncation(total: int) -> str:
    if total <= GHOST_LIMIT:
        return ""
    return (
        f"showing the {GHOST_LIMIT} strongest of {total} ghost edge(s); "
        f"the rest are in .ragged/{GRAPH_NAME}"
    )


def _orphans(concepts: list[okf.Concept]) -> list[str]:
    linked: set[str] = set()
    for concept in concepts:
        for _, target in okf.cross_links(concept.body, f"{concept.concept_id}.md"):
            if target != concept.concept_id:
                linked.add(target)
    return [c.concept_id for c in concepts if c.concept_id not in linked]
