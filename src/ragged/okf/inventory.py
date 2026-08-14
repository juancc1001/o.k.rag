"""What is already in a bundle: id, title, description, status.

The format layer's read side. Synthesis needs it so the prompt knows what the
bundle already covers, link-weave needs it to know what can legally be linked
to, and index generation needs the titles.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .concept import Concept, parse

RESERVED = {"index.md", "log.md"}


@dataclass(frozen=True)
class ConceptRef:
    concept_id: str
    title: str
    description: str
    status: str | None = None

    @property
    def parent(self) -> str | None:
        return self.concept_id.rsplit("/", 1)[0] if "/" in self.concept_id else None


def read_concepts(bundle_root: Path) -> list[Concept]:
    """Every concept in the bundle, parsed, in path order.

    A file that does not parse is skipped rather than raised on: this feeds
    prompts, indexes and the graph, and one malformed concept must not stop any
    of them.
    """
    concepts: list[Concept] = []
    if not bundle_root.exists():
        return concepts
    for path in sorted(bundle_root.rglob("*.md")):
        if path.name in RESERVED:
            continue
        concept_id = path.relative_to(bundle_root).with_suffix("").as_posix()
        try:
            # utf-8-sig: a hand-edited concept may carry a BOM
            concepts.append(parse(path.read_text(encoding="utf-8-sig"), concept_id))
        except (OSError, ValueError):
            continue
    return concepts


def inventory(bundle_root: Path) -> list[ConceptRef]:
    """Every concept in the bundle as `(id, title, description, status)`."""
    return [
        ConceptRef(
            concept_id=concept.concept_id,
            title=concept.title or concept.concept_id,
            description=concept.description or "",
            status=concept.status,
        )
        for concept in read_concepts(bundle_root)
    ]
