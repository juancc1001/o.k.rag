"""The staged emit and its pending-commit record (DESIGN §6.6, §13 M5 item 6).

`okf/writer.py` already makes one file's write atomic — temp file plus rename. It
is the *set* of files that needed this, and the claim here is deliberately weaker
than atomic: a multi-file change cannot be made atomic on Windows, and pretending
otherwise would be worse than not trying. So the goal is **recoverable**.

**The unit is the source, not the run.** One source's emit commits on its own:
the concepts it wrote under their new ids, the removal of the stale output its
rename left behind, and that source's ledger entry — together or not at all. The
hazard that makes this worth fencing lives *inside* one source's emit. Concept
ids derive from model-written titles, so a rebuild renames that source's tree
wholesale, and the dangerous window is between writing the new names and removing
the old ones: a half-applied emit is not a subset of the old tree, it is a mix of
two namings, and stale-output cleanup (§5) reconciles against a complete emit or
not at all. Across sources there is no such window — source A's new concepts
beside source B's untouched old ones is a complete, self-consistent bundle — so
making the *run* the unit bought nothing and cost the synthesis of every source
an interrupted run had already paid for.

A run stages one unit at a time and each commit is fenced by a record on disk:

  1. save the ledger into `.okragged/staging/manifest.json`
  2. write `.okragged/pending-commit.json`   ← from here the commit is replayable
  3. move each staged concept into `bundle/`
  4. delete the stale output the record names
  5. `.okragged/staging/manifest.json` → `.okragged/manifest.json`
  6. delete the staging area and the record

Every step is idempotent and guarded by what is on disk, so `reconcile` replays
from wherever a crash landed. Nothing ever moves or deletes the published tree as
a whole, so unlike a whole-run swap there is no moment with no bundle at all.
Which of the two answers — finish, or roll back — is right follows from the
record alone, and that is the reason it exists:

- **A record, with or without a staging area: finish.** The record is written
  only once the unit is complete, so its presence means the unit was worth
  publishing and only the placing is unfinished.
- **A staging area with no record: roll back.** That unit was interrupted before
  it earned one. The sources committed before it stand, only that one is redone,
  and its staged files are left in the sidecar — not deleted at the moment of
  failure — until the next run reports them and clears them.

Step 1 is why the ledger stages too. The ledger is only true once every file it
describes has landed, so it lands with the unit or not at all. A ledger that
outran the bundle would have the next run's content-hash skip believe work was
done that was not, and the corpus would quietly lose those sources.
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
"""The staged tree's name inside the staging area. Fixed rather than taken from
`[bundle].root`, which may be a nested path; the record carries the destination."""
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
    """Open an empty staging area for the run's first unit.

    Nothing is copied and nothing is created. The area holds one unit's output —
    the concepts a single source produced — and the commit *places* those files
    into the published tree rather than replacing it. Seeding a copy of the whole
    bundle was what a whole-run swap needed in order to remove stale output from
    it and read indexes off it; a per-source commit names the files it removes,
    and the indexes are read off the published bundle where they always were.
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
    """Publish one unit: its concepts, its stale output, and the ledger entry.

    A unit is one source's emit, one concept the weave rewrote, or one vanished
    source's deprecation — never the whole run. `place` and `remove` are concept
    ids: the staged file for `x/y` is `staging/bundle/x/y.md`, so one relative
    path names it under either root.

    The ledger is saved into the staging area first and moved last, so what the
    record fences is the whole unit. Ordering inside the fence is *place, then
    remove*: within the window, both namings on disk is a superset of the truth
    and neither of them is not.
    """
    record = _record(area, unit=unit, place=place, remove=remove)
    ledger.save(area.ledger)
    _write_pending(area.pending, record)
    _apply(area, record)


def discard(area: Area) -> None:
    """Throw the staging area away. Never touches `bundle/` or the ledger."""
    _clear(area.root)


def reconcile(config: Config) -> str:
    """Finish or roll back what a previous run left behind, and say which.

    Called under the lock, before a run stages anything of its own. Returns a note
    for the caller to report: a build that silently repaired this would be hiding
    that an earlier one died, which is the fact behind whatever else looks wrong.
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
    """Steps 3-6, from wherever the last attempt got to.

    Guarded by what is on disk rather than by a step counter in the record: a
    counter would be a second thing that has to be written at the right moment,
    and every step here is already idempotent.
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
    """Atomically, and flushed: it is the only thing standing between a crash
    mid-commit and a source whose tree is a mix of two namings."""
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

    An unreadable record is not "no record": its *presence* is the fact that
    matters, and a corrupt one read as debris would roll back a commit that is
    half applied. So it degrades to the widest safe reading of what it must have
    said — place every concept the staging area still holds, remove nothing. The
    removals are lost that way and the stale output they named survives as an
    orphan, which is the direction that keeps a concept rather than losing one.
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
