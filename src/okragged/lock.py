"""One writer per project — `.okragged/lock.json` (DESIGN §5, §13 M5 item 6).

After M5 there are three writers to one directory: `okragged build`, the MCP
server's `add_source`, and the Python API. Interleaving them is worse here than
it sounds, because concept ids derive from model-written titles: two runs do not
overwrite each other's names, they write two different trees into one directory.

The mechanism is exclusive creation. `open(path, "x")` fails when the file
already exists, it fails atomically, and it does so on both platforms — `fcntl`
does not, and Windows is the primary one.

What the file *holds* is the half that makes it usable. A lock nobody can clear
is a worse failure than the one it prevents, so the record names its holder and a
run that finds a dead one reclaims it rather than asking a human to delete a
file. Sidecar state, so an unreadable record means "no holder" rather than an
exception.

**Read paths never take it.** `search`, `read_concept`, `lint`, `check` and
`serve` keep answering while a write is in flight: reading a directory of
markdown files needs no permission from anyone, and a reader that could be
refused by a build would undo the point of the format.
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
"""How old a lock held by *another machine* may get before it is reclaimed.

Only ever used for a project on a network share, where the pid in the record
names a process on a host this one cannot ask about. Age is a poor test — it is
the reason a long build could be reclaimed under itself — so it is the fallback
and never the rule, and it is generous enough that the run it would misjudge is
one that has already been running for a working day.
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
    """Empty unless a dead holder's lock was reclaimed. Worth reporting: it says
    a previous run died, which is the fact behind whatever else looks wrong."""


@contextmanager
def hold(sidecar_root: Path, operation: str) -> Iterator[Held]:
    """Hold the project for one writer, or raise `LockHeld` naming the other one.

    **Not reentrant, deliberately.** `_alive` reads our own pid as live, because
    two `add_source` calls in one MCP server process are exactly what this exists
    to serialize. So nesting two `hold`s in one process self-refuses with a
    message naming its own pid — never wrap a caller of `pipeline.ingest` or
    `vectors.index` in another one. The deferred `POST /api/ingest` job model
    (§11) is the obvious future caller that would be tempted.
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
                # somebody else reclaimed it in the gap; refusing is right, and
                # a loop here would be a race with no upper bound
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
    """Drop the lock, unless it is no longer ours.

    A run whose lock was reclaimed while it was still going must not delete the
    lock of whoever reclaimed it — that would leave two writers with no lock at
    all, which is worse than the state this whole file exists to prevent.
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
        # us. Refusing is right: two `add_source` calls in one MCP server process
        # are the case this exists for, and treating our own pid as dead would let
        # both through. It also reads a recycled pid as live, which costs one
        # refused run with the pid in the message and fixes itself on the next.
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

    Deliberately not `os.kill(pid, 0)`: on Windows that is not a no-op signal
    probe, it calls `TerminateProcess` with the signal as the exit code, so the
    liveness test would kill the process it asked about.
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
        # a process that exited with 259 reads as alive. The cost is one refused
        # run with a message naming the pid; the cost of the other error is two
        # writers in one bundle
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
