"""The staged emit and its pending-commit record (DESIGN §6.6, §13 M5 item 6).

`okf/writer.py` already makes one file's write atomic — temp file plus rename. It
is the *tree* that needed this, and the claim here is deliberately weaker than
atomic: a multi-file directory swap cannot be made atomic on Windows, and
pretending otherwise would be worse than not trying, because
`bundle/` → `bundle.old/` → `staging/` → `bundle/` has a window in which there is
no bundle at all.

So the goal is **recoverable**. A run emits into `.okragged/staging/`, and the
swap is fenced by a record on disk:

  1. write `.okragged/pending-commit.json`   ← from here the swap is replayable
  2. `bundle/` → `.okragged/superseded/`
  3. `.okragged/staging/bundle/` → `bundle/`
  4. `.okragged/staging/manifest.json` → `.okragged/manifest.json`
  5. delete the staging area, the superseded tree, and the record

Every step is idempotent and guarded by what is on disk, so `reconcile` replays
from wherever a crash landed. Which of the two answers — finish, or roll back — is
right follows from the record alone, and that is the reason it exists:

- **A record, with or without a staging area: finish.** The record is written only
  once the staged tree is complete, so its presence means the tree was worth
  publishing and the swap is the only thing unfinished.
- **A staging area with no record: roll back.** The run was interrupted before it
  earned one. The previous tree stands, and the staged work is left in the
  sidecar — unpublished, but not deleted at the moment of failure — until the next
  run reports it and clears it.

Step 4 is why the ledger stages too. The ledger is only true once every file it
describes has landed, so the two commit together or neither does. A ledger that
outran the bundle would have the next run's content-hash skip believe work was
done that was not, and the corpus would quietly lose those sources.

Why any of this matters more here than in a tool that overwrites files in place:
concept ids derive from model-written titles, so a rebuild renames the whole tree
as a matter of course. A half-applied emit is therefore not a subset of the old
tree — it is a mix of two namings, and stale-output cleanup (§5) reconciles
against a *complete* emit or not at all.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .ledger import LEDGER_NAME

STAGING = "staging"
"""`.okragged/staging/` — where a run in flight emits."""
SUPERSEDED = "superseded"
"""`.okragged/superseded/` — the previous tree, until the commit finishes."""
PENDING_NAME = "pending-commit.json"
BUNDLE = "bundle"
"""The staged tree's name inside the staging area. Fixed rather than taken from
`[bundle].root`, which may be a nested path; the record carries the destination."""
VERSION = 1


@dataclass(frozen=True)
class Area:
    """Where one run emits, and where its commit will publish to."""

    project_root: Path
    live: Path
    """The real `bundle/`. Not `config.bundle_root`, which points at `bundle`
    below while a run is staged."""
    root: Path
    bundle: Path
    ledger: Path
    superseded: Path
    pending: Path


def area_for(config: Config) -> Area:
    root = config.sidecar_root / STAGING
    return Area(
        project_root=config.project_root,
        live=config.project_root / config.bundle.root,
        root=root,
        bundle=root / BUNDLE,
        ledger=root / LEDGER_NAME,
        superseded=config.sidecar_root / SUPERSEDED,
        pending=config.sidecar_root / PENDING_NAME,
    )


def prepare(config: Config) -> Area:
    """Open a staging area seeded with the bundle as it stands.

    A copy, and the cost is honest: a run touching one source of a hundred still
    copies the hundred. What it buys is that the emit is a whole tree rather than
    a sequence of edits to the published one — indexes are read off the bundle
    (§6.6) and stale output is removed from it, so a staged emit that started
    from an empty directory would report every untouched concept as gone.

    Nothing is created when there is no bundle yet: an empty directory published
    by a run that synthesized nothing is a louder lie than no directory at all.
    """
    area = area_for(config)
    discard(area)
    area.root.mkdir(parents=True, exist_ok=True)
    if area.live.is_dir():
        shutil.copytree(area.live, area.bundle)
    return area


def commit(area: Area) -> bool:
    """Publish the staged tree and its ledger together. False if there was none."""
    if not area.bundle.exists() and not area.ledger.exists():
        discard(area)
        return False
    record = _record(area)
    _write_pending(area.pending, record)
    _apply(area, record)
    return True


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
        _apply(area, record)
        return f"completed a bundle commit interrupted at {at}"

    notes: list[str] = []
    if area.root.exists():
        staged = sum(1 for _ in area.bundle.rglob("*.md")) if area.bundle.is_dir() else 0
        discard(area)
        notes.append(
            f"discarded {staged} concept file(s) staged by an interrupted run - "
            "it never reached its commit, so the previous bundle stands"
        )
    if not area.live.exists() and area.superseded.is_dir():
        # no record and no staged tree, but the previous one is still parked:
        # somebody deleted the record mid-swap, and a project with no bundle at
        # all is not a state to leave behind
        area.live.parent.mkdir(parents=True, exist_ok=True)
        os.rename(area.superseded, area.live)
        notes.append(f"restored the previous bundle from .okragged/{SUPERSEDED}")
    _clear(area.superseded)
    return "; ".join(notes)


def _record(area: Area) -> dict:
    """What the swap needs to be replayable, as project-relative paths."""
    return {
        "version": VERSION,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "pid": os.getpid(),
        "bundle": _rel(area.project_root, area.live),
        "staged_bundle": _rel(area.project_root, area.bundle),
        "staged_ledger": _rel(area.project_root, area.ledger),
        "ledger": _rel(area.project_root, area.pending.parent / LEDGER_NAME),
        "superseded": _rel(area.project_root, area.superseded),
    }


def _apply(area: Area, record: dict) -> None:
    """Steps 2-5, from wherever the last attempt got to.

    Guarded by the filesystem rather than by a step counter in the record: what is
    on disk is the truth, and a counter would be a second thing that has to be
    written at the right moment.
    """
    root = area.project_root
    live = root / record["bundle"]
    staged = root / record["staged_bundle"]
    superseded = root / record["superseded"]
    staged_ledger = root / record["staged_ledger"]
    ledger = root / record["ledger"]

    if staged.is_dir():
        if live.exists():
            _clear(superseded)
            os.rename(live, superseded)
        live.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staged, live)
    elif not live.exists() and superseded.is_dir():
        # the staged tree is already published or already gone, and the previous
        # one is parked: put it back rather than leave no bundle at all
        live.parent.mkdir(parents=True, exist_ok=True)
        os.rename(superseded, live)

    if staged_ledger.exists():
        ledger.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_ledger, ledger)

    _clear(staged.parent)
    _clear(superseded)
    area.pending.unlink(missing_ok=True)


def _write_pending(path: Path, record: dict) -> None:
    """Atomically, and flushed: it is the only thing standing between a crash
    mid-swap and a tree that is a mix of two namings."""
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
    matters, and a corrupt one would otherwise be read as debris and roll back a
    swap that is half applied. So it degrades to the layout it must have had.
    """
    if not area.pending.exists():
        return None
    try:
        record = json.loads(area.pending.read_text(encoding="utf-8-sig"))
        if isinstance(record, dict) and record.get("version") == VERSION:
            return record
    except (OSError, ValueError):
        pass
    return {**_record(area), "at": "an unrecorded time (the record was unreadable)"}


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
