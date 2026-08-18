from __future__ import annotations

import os
import posixpath
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .concept import Concept, parse, render
from .inventory import ConceptRef, inventory

LOG_HEADING = "# Update Log"


def write_concept(bundle_root: Path, concept: Concept) -> Path:
    path = bundle_root / f"{concept.concept_id}.md"
    _atomic_write(path, render(concept))
    return path


def set_status(
    bundle_root: Path, concept_id: str, status: str, out_root: Path | None = None
) -> bool:
    """Rewrite one concept's `status`, leaving the body and every other field.

    Used to mark a concept deprecated when its source disappears: the spec keeps
    such concepts for links and history rather than deleting them.

    `out_root` writes the rewrite somewhere other than where it was read — a
    staging area whose commit publishes it — leaving `bundle_root` untouched.
    """
    path = bundle_root / f"{concept_id}.md"
    if not path.exists():
        return False
    concept = parse(path.read_text(encoding="utf-8-sig"), concept_id)
    if concept.status == status:
        return False
    concept.status = status
    _atomic_write((out_root or bundle_root) / f"{concept_id}.md", render(concept))
    return True


def delete_concept(bundle_root: Path, concept_id: str) -> bool:
    """Remove a concept file, and any directory it leaves with nothing to index."""
    path = bundle_root / f"{concept_id}.md"
    if not path.exists():
        return False
    path.unlink()
    _prune_empty(bundle_root, path.parent)
    return True


def _prune_empty(bundle_root: Path, directory: Path) -> None:
    """A directory holding only its own index.md is no longer a section."""
    while directory != bundle_root and directory.is_relative_to(bundle_root):
        if any(p.name != "index.md" for p in directory.iterdir()):
            return
        (directory / "index.md").unlink(missing_ok=True)
        directory.rmdir()
        directory = directory.parent


@dataclass
class _Node:
    concepts: list[ConceptRef] = field(default_factory=list)
    subdirs: list[str] = field(default_factory=list)


def write_indexes(bundle_root: Path, okf_version: str, title: str) -> list[Path]:
    """Generate `index.md` for every directory in the bundle.

    Read off disk rather than accumulated during the run, so a directory whose
    concepts were skipped this time is indexed exactly like one that was just
    written, and an entry can never disagree with the concept it points at:
    every label and description is the target's own frontmatter.

    Only the bundle root carries frontmatter (E3).
    """
    nodes = _tree(inventory(bundle_root))
    written: list[Path] = []
    # deepest first (§6.6 bottom-up): a parent entry states what is below it
    for key in sorted(nodes, key=lambda k: (-k.count("/"), k)):
        if key and not nodes[key].concepts and not nodes[key].subdirs:
            continue
        heading = title if not key else _heading(nodes, key)
        body = _render_index(nodes, key, heading, okf_version if not key else None)
        path = bundle_root / key / "index.md" if key else bundle_root / "index.md"
        _atomic_write(path, body)
        written.append(path)
    return written


def _tree(refs: list[ConceptRef]) -> dict[str, _Node]:
    nodes: dict[str, _Node] = {"": _Node()}
    for ref in refs:
        directory = posixpath.dirname(ref.concept_id)
        parts = directory.split("/") if directory else []
        for depth in range(len(parts) + 1):
            nodes.setdefault("/".join(parts[:depth]), _Node())
        nodes[directory].concepts.append(ref)
    for key in nodes:
        if key:
            nodes[posixpath.dirname(key)].subdirs.append(key)
    for node in nodes.values():
        node.subdirs.sort()
    return nodes


def _descendants(nodes: dict[str, _Node], key: str) -> int:
    return len(nodes[key].concepts) + sum(_descendants(nodes, s) for s in nodes[key].subdirs)


def _heading(nodes: dict[str, _Node], key: str) -> str:
    """A subdirectory is named by the concept that owns it, when there is one."""
    parent = nodes.get(posixpath.dirname(key))
    if parent is not None:
        for ref in parent.concepts:
            if ref.concept_id == key:
                return ref.title
    return _label(key)


def _render_index(
    nodes: dict[str, _Node], key: str, heading: str, okf_version: str | None
) -> str:
    lines: list[str] = []
    if okf_version is not None:
        lines += ["---", f'okf_version: "{okf_version}"', "---", ""]
    lines += [f"# {heading}", ""]

    node = nodes[key]
    owned: set[str] = set()
    for ref in node.concepts:
        leaf = ref.concept_id.rsplit("/", 1)[-1]
        entry = f"- [{ref.title}](./{leaf}.md)"
        if ref.status == "deprecated":
            entry += " *(deprecated)*"
        if ref.description:
            entry += f" - {ref.description}"
        if ref.concept_id in nodes:
            # otherwise an agent reading this index alone concludes the bundle
            # holds one concept here when it holds a whole subtree
            count = _descendants(nodes, ref.concept_id)
            entry += f" ([{count} more](./{leaf}/index.md))"
            owned.add(ref.concept_id)
        lines.append(entry)

    for sub in node.subdirs:
        if sub in owned:
            continue
        leaf = sub.rsplit("/", 1)[-1]
        lines.append(f"- [{_label(sub)}](./{leaf}/index.md) - {_descendants(nodes, sub)} concept(s)")

    return "\n".join(lines).rstrip() + "\n"


def append_log(bundle_root: Path, date: str, entries: list[str]) -> Path:
    """Insert entries under an ISO date heading, newest date first."""
    path = bundle_root / "log.md"
    # utf-8-sig: log.md may have been hand-edited; a BOM must not survive the rewrite
    existing = path.read_text(encoding="utf-8-sig") if path.exists() else f"{LOG_HEADING}\n"
    lines = existing.splitlines()
    if LOG_HEADING not in lines:
        lines = [LOG_HEADING, ""] + lines

    heading = f"## {date}"
    bullets = [f"- {entry}" for entry in entries]

    if heading in lines:
        at = lines.index(heading) + 1
        while at < len(lines) and lines[at].strip() == "":
            at += 1
        lines[at:at] = bullets
    else:
        at = lines.index(LOG_HEADING) + 1
        lines[at:at] = ["", heading] + bullets

    _atomic_write(path, "\n".join(lines).rstrip() + "\n")
    return path


def _label(concept_id: str) -> str:
    """Last-resort name for a directory nothing owns. A concept uses its `title`.

    Deriving a label from the slug folds accents away and then applies English
    title-case, which reads badly in any other language.
    """
    return concept_id.rsplit("/", 1)[-1].replace("-", " ").capitalize()


def _atomic_write(path: Path, text: str) -> None:
    """Temp file, flush to disk, rename.

    The fsync is what makes this usable as a commit *payload*. A staged concept is
    published by `staging._apply` on the strength of a pending record that is
    itself fsynced, so without this the record could outlive the content it names
    and the replay would place a truncated concept. Model calls dominate a run by
    orders of magnitude, so the cost does not show.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
