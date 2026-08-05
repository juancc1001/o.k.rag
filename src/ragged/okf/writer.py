from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .concept import Concept, parse, render

LOG_HEADING = "# Update Log"


def write_concept(bundle_root: Path, concept: Concept) -> Path:
    path = bundle_root / f"{concept.concept_id}.md"
    _atomic_write(path, render(concept))
    return path


def set_status(bundle_root: Path, concept_id: str, status: str) -> bool:
    """Rewrite one concept's `status`, leaving the body and every other field.

    Used to mark a concept deprecated when its source disappears: the spec keeps
    such concepts for links and history rather than deleting them.
    """
    path = bundle_root / f"{concept_id}.md"
    if not path.exists():
        return False
    concept = parse(path.read_text(encoding="utf-8-sig"), concept_id)
    if concept.status == status:
        return False
    concept.status = status
    _atomic_write(path, render(concept))
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


def write_root_index(bundle_root: Path, okf_version: str, title: str, entries: list[tuple[str, str]]) -> Path:
    """Bundle-root index.md — the only index.md allowed to carry frontmatter."""
    lines = [
        "---",
        f'okf_version: "{okf_version}"',
        "---",
        "",
        f"# {title}",
        "",
    ]
    for link, description in entries:
        suffix = f" - {description}" if description else ""
        lines.append(f"- [{_label(link)}](./{link}.md){suffix}")
    path = bundle_root / "index.md"
    _atomic_write(path, "\n".join(lines).rstrip() + "\n")
    return path


def write_child_index(
    bundle_root: Path, parent_id: str, title: str, entries: list[tuple[str, str]]
) -> Path:
    """A subdirectory index.md. Must NOT carry frontmatter — only the bundle root may (E3)."""
    lines = [f"# {title}", ""]
    for link, description in entries:
        suffix = f" - {description}" if description else ""
        lines.append(f"- [{_label(link)}](./{link}.md){suffix}")
    path = bundle_root / parent_id / "index.md"
    _atomic_write(path, "\n".join(lines).rstrip() + "\n")
    return path


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
    return concept_id.rsplit("/", 1)[-1].replace("-", " ").title()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
