"""The staged emit and its pending-commit record (DESIGN.md §6.6).

`okf/writer.py` already makes one file's write atomic (temp file plus rename).
This module does the same for a *set* of files, and deliberately settles for
weaker than atomic: Windows has no way to commit several files at once, so the
goal here is **recoverable**, not atomic.

**The unit is one source, not the whole run.** A source's emit renames its
whole tree at once, because concept ids come from model-written titles — so a
half-applied emit isn't a subset of the old tree, it's a mix of old and new
names. That's the window worth fencing. Across sources there's no such window:
source A's new concepts beside source B's untouched old ones is already a
valid bundle, so fencing the whole run would only have thrown away work an
interrupted run had already paid for.

A run stages one unit at a time; each commit is fenced by a record on disk:

  1. save the ledger into `.okragged/staging/manifest.json`
  2. write `.okragged/pending-commit.json`   <- from here the commit is replayable
  3. move each staged concept into `bundle/`
  4. delete the stale output the record names
  5. `.okragged/staging/manifest.json` -> `.okragged/manifest.json`
  6. delete the staging area and the record

Every step is idempotent and guarded by what's already on disk, so `reconcile`
can replay from wherever a crash landed:

- **A pending-commit record exists: finish it.** It's only written once the
  unit is complete, so its presence means the unit is worth publishing.
- **A staging area with no record: roll back.** That unit was interrupted
  before it earned one; its staged files wait in the sidecar until the next
  run reports and clears them.

The ledger stages too (step 1), for the same reason: it's only true once every
file it describes has landed. A ledger that outran the bundle would make the
next run's content-hash skip believe work was done that wasn't, silently
losing sources.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .ledger import LEDGER_NAME, Ledger
from .okf import delete_concept

STAGING = "staging"
"""`.okragged/staging/` — where the unit in flight writes."""
PENDING_NAME = "pending-commit.json"
BUNDLE = "bundle"
"""The staged tree's name inside the staging area — fixed, not `[bundle].root`
(which may be a nested path); the record carries the real destination."""
VERSION = 2


@dataclass(frozen=True)
class Area:
    """Where one unit stages its output, and where its commit will publish it."""

    project_root: Path
    live: Path
    """The published `bundle/`. Read from all run long; written only by a commit."""
    root: Path
    bundle: Path
    ledger: Path
    pending: Path


def area_for(config: Config) -> Area:
    root = config.sidecar_root / STAGING
    return Area(
        project_root=config.project_root,
        live=config.project_root / config.bundle.root,
        root=root,
        bundle=root / BUNDLE,
        ledger=root / LEDGER_NAME,
        pending=config.sidecar_root / PENDING_NAME,
    )


def prepare(config: Config) -> Area:
    """Opens an empty staging area for the run's first unit.

    Nothing is copied here — the area holds one unit's output (the concepts one
    source produced), and the commit *places* those files into the published
    tree rather than replacing it wholesale.
    """
    area = area_for(config)
    discard(area)
    return area


def commit(
    area: Area,
    ledger: Ledger,
    *,
    unit: str,
    place: Sequence[str],
    remove: Sequence[str] = (),
) -> None:
    """Publishes one unit: its concepts, its stale output, and the ledger entry.

    A unit is one source's emit, one concept link-weave rewrote, or one vanished
    source's deprecation — never the whole run. `place` and `remove` are concept
    ids; the staged file for `x/y` is `staging/bundle/x/y.md`, so one relative
    path names it under either root.

    Order matters: place before remove, so mid-commit both namings exist on
    disk at once rather than a gap where neither does.
    """
    record = _record(area, unit=unit, place=place, remove=remove)
    ledger.save(area.ledger)
    _write_pending(area.pending, record)
    _apply(area, record)


def discard(area: Area) -> None:
    """Throw the staging area away. Never touches `bundle/` or the ledger."""
    _clear(area.root)


def reconcile(config: Config) -> str:
    """Finishes or rolls back whatever a previous run left staged, and reports which.

    Called under the lock, before a run stages anything of its own. Silently
    repairing this would hide that an earlier run died — the fact behind
    whatever else looks wrong — so it always returns a note to report.
    """
    area = area_for(config)
    record = _read_pending(area)
    if record is not None:
        at = record.get("at") or "an unrecorded time"
        unit = record.get("unit") or "an unnamed unit"
        _apply(area, record)
        return f"completed the commit of {unit}, interrupted at {at}"

    if not area.root.exists():
        return ""
    staged = len(_staged_ids(area))
    discard(area)
    if not staged:
        return ""
    return (
        f"discarded {staged} concept file(s) staged by an interrupted run - that "
        "source never reached its commit, so the sources committed before it "
        "stand and only that one is redone"
    )


def _record(
    area: Area,
    *,
    unit: str,
    place: Sequence[str],
    remove: Sequence[str],
    at: str | None = None,
) -> dict:
    """What the commit needs to be replayable, as project-relative paths."""
    return {
        "version": VERSION,
        "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "pid": os.getpid(),
        "unit": unit,
        "bundle": _rel(area.project_root, area.live),
        "staged_bundle": _rel(area.project_root, area.bundle),
        "staged_ledger": _rel(area.project_root, area.ledger),
        "ledger": _rel(area.project_root, area.pending.parent / LEDGER_NAME),
        "place": list(place),
        "remove": list(remove),
    }


def _apply(area: Area, record: dict) -> None:
    """Runs steps 3-6, resuming from wherever the last attempt got to.

    Guarded by what's on disk, not a step counter — a counter would be one more
    thing that has to be written at the right moment, and every step here is
    already idempotent.
    """
    root = area.project_root
    live = root / record["bundle"]
    staged = root / record["staged_bundle"]

    for concept_id in record.get("place") or []:
        source = staged / f"{concept_id}.md"
        if not source.is_file():
            continue  # an earlier attempt already placed it
        destination = live / f"{concept_id}.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
    for concept_id in record.get("remove") or []:
        delete_concept(live, concept_id)

    staged_ledger = root / record["staged_ledger"]
    if staged_ledger.is_file():
        ledger = root / record["ledger"]
        ledger.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_ledger, ledger)

    _clear(area.root)
    area.pending.unlink(missing_ok=True)


def _write_pending(path: Path, record: dict) -> None:
    """Writes the pending-commit record atomically and fsynced — it's the only
    thing standing between a crash mid-commit and a tree stuck mixing old and
    new concept names.

    What it fences must be durable too: `okf/writer._atomic_write` and
    `Ledger.save` both fsync, or a record could outlive the content it names
    and replay a commit over a truncated concept.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_pending(area: Area) -> dict | None:
    """The record of a commit that was in flight, or None if none was.

    An unreadable record still means "a commit was interrupted", not "no
    record" — so a corrupt one degrades to the widest safe reading rather than
    being treated as debris: place every concept still in the staging area,
    remove nothing. That loses the removals (their stale output survives as an
    orphan) rather than the alternative, which would lose a concept.
    """
    if not area.pending.exists():
        return None
    try:
        record = json.loads(area.pending.read_text(encoding="utf-8-sig"))
        if isinstance(record, dict) and record.get("version") == VERSION:
            return record
    except (OSError, ValueError):
        pass
    return _record(
        area,
        unit="an unnamed unit (the record was unreadable)",
        place=_staged_ids(area),
        remove=[],
        at="an unrecorded time",
    )


def _staged_ids(area: Area) -> list[str]:
    """The concept ids the staging area holds, for a record that cannot be read."""
    if not area.bundle.is_dir():
        return []
    return sorted(
        path.relative_to(area.bundle).as_posix()[: -len(".md")]
        for path in area.bundle.rglob("*.md")
        if path.name not in {"index.md", "log.md"}
    )


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()  # outside the project: absolute is all we have


def _clear(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
