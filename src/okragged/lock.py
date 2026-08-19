"""One writer per project — `.okragged/lock.json` (DESIGN.md §5).

Three things can write to a project directory: `okragged build`, the MCP
server's `add_source`, and the Python API. Letting two of them interleave is
worse than an ordinary race here, because concept ids are derived from
model-written titles — two runs don't overwrite each other's names, they each
write a different tree into the same directory.

The locking mechanism is exclusive file creation (`open(path, "x")`): it fails
atomically when the file already exists, the same way on Windows and POSIX —
`fcntl` doesn't, and Windows is the primary platform here.

The lock file records who holds it, so a run that finds a dead holder's lock
can reclaim it automatically instead of asking a human to delete a file by
hand. It's sidecar state, so an unreadable record just means "no holder", never
a crash.

**Read paths never take this lock** — `search`, `read_concept`, `lint`, `check`
and `serve` keep answering while a write is in flight, since reading markdown
files needs nobody's permission.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

LOCK_NAME = "lock.json"
VERSION = 1

FOREIGN_STALE_SECONDS = 6 * 60 * 60
"""How old a lock held by *another machine* may get before it's reclaimed.

Only applies on a network share, where the pid names a process on a host this
one can't query directly. Age is a poor liveness test — a long build could get
reclaimed out from under itself — so it's the fallback, not the rule, and
generous enough that a wrongly-reclaimed run has already run a full workday.
"""


class LockHeld(RuntimeError):
    """Another writer holds the project. The message names what holds it."""


@dataclass(frozen=True)
class Holder:
    operation: str
    pid: int
    host: str
    started: str

    def describe(self) -> str:
        return f"{self.operation}, pid {self.pid} on {self.host}, since {self.started}"


@dataclass(frozen=True)
class Held:
    """The lock this run holds, plus what it had to clear away to get it."""

    holder: Holder
    note: str = ""
    """Empty unless a dead holder's lock was reclaimed — worth surfacing, since
    it explains why anything else looks stale."""


@contextmanager
def hold(sidecar_root: Path, operation: str) -> Iterator[Held]:
    """Holds the project for one writer, or raises `LockHeld` naming the other one.

    Not reentrant: nesting two `hold()` calls in the same process self-refuses,
    since `_alive` treats our own pid as live on purpose — that's what serializes
    two `add_source` calls inside one MCP server process. Never wrap a call to
    `pipeline.ingest` or `vectors.index` in another `hold()`.
    """
    path = sidecar_root / LOCK_NAME
    holder = Holder(
        operation=operation,
        pid=os.getpid(),
        host=socket.gethostname(),
        started=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    note = _claim(path, holder)
    try:
        yield Held(holder=holder, note=note)
    finally:
        _release(path, holder)


def _claim(path: Path, holder: Holder) -> str:
    """Create the lock file, reclaiming a dead holder's once. Returns a note."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": VERSION, **asdict(holder)}, indent=2) + "\n"
    note = ""
    for attempt in (1, 2):
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
            return note
        except FileExistsError:
            other = _read(path)
            if other is not None and _alive(other):
                raise LockHeld(
                    f"another okragged run holds this project: {other.describe()}. "
                    "Reads are unaffected - `search`, `read_concept`, `lint` and "
                    "`check` do not take the lock."
                ) from None
            if attempt == 2:
                # somebody else reclaimed it in the gap; retrying would race with
                # no upper bound
                raise LockHeld(
                    "another okragged run claimed this project while a dead "
                    f"holder's lock was being cleared: {path}"
                ) from None
            note = (
                f"reclaimed a lock left by a run that is gone ({other.describe()})"
                if other is not None
                else f"reclaimed an unreadable lock file at {path}"
            )
            path.unlink(missing_ok=True)
    raise AssertionError("unreachable")  # pragma: no cover


def _release(path: Path, holder: Holder) -> None:
    """Drops the lock, unless it's no longer ours — a run whose lock got
    reclaimed while it was still going must not delete the reclaimer's lock,
    or two writers end up with none at all.
    """
    current = _read(path)
    if current is None or current == holder:
        path.unlink(missing_ok=True)


def _read(path: Path) -> Holder | None:
    """The recorded holder, or None when there is nothing legible to obey."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None
    try:
        return Holder(
            operation=str(data["operation"]),
            pid=int(data["pid"]),
            host=str(data["host"]),
            started=str(data["started"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _alive(holder: Holder) -> bool:
    if holder.host != socket.gethostname():
        return not _older_than(holder.started, FOREIGN_STALE_SECONDS)
    if holder.pid == os.getpid():
        # us — refusing is the point (see hold()'s docstring). This also reads a
        # recycled pid as live, which costs one refused run and self-corrects
        # next time.
        return True
    return _process_alive(holder.pid)


def _older_than(started: str, seconds: float) -> bool:
    try:
        at = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return True  # no usable timestamp is not a reason to wedge
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).total_seconds() > seconds


def _process_alive(pid: int) -> bool:
    """Whether `pid` is still running on this host.

    Not `os.kill(pid, 0)`: on Windows that calls `TerminateProcess` rather than
    probing, so the liveness check would kill the process it's asking about.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # somebody else's process, and it exists
    return True


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # denied means it exists and belongs to another user; anything else
        # (ERROR_INVALID_PARAMETER) means there is no such process
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True  # cannot tell: refusing a build beats corrupting one
        # a process that happened to exit with code 259 reads as alive — cheaper
        # than misjudging a live one as dead
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
