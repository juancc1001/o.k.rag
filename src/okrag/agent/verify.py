"""`verify_concept` (DESIGN.md §13 M8, criterion 5): appends one confirmation
entry to a concept's `verified` list, attributed to the caller's own
OAuth-verified identity rather than to a string the caller supplied.

**Append-only, and `status` is untouched.** Promoting `draft` -> `stable`
stays a separate, human-owned file edit, unchanged since M5 - this tool adds
one more fact to the trust record, it does not decide what the concept's
lifecycle state is.

**Takes the project lock, not `staging.py`.** This is read-modify-write on
one *existing*, already-published file, unlike `add_source`'s recording path,
which claims a brand-new path and has nothing to race. Two concurrent
verifications of the same concept would otherwise silently lose one entry -
the same lost-update class criterion 3 closed for `sources/`, now on
`bundle/`. The project lock is the only locking primitive this codebase has
("one writer per project"); reusing it is simpler than a new per-concept lock,
and the cost - blocking briefly against a concurrent `ingest`/`vectors.index`/
synthesizing `add_source` - is one every other writer already accepts.
`staging.py`'s per-source commit machinery is not needed: it exists for
recoverability across a *set* of model-named files, and here there is exactly
one file at a path that never changes, so `_atomic_write`'s fsync already
makes this single write crash-safe.

**`ledger.py` needed no changes.** `Ledger.protection` already treats any
concept carrying a `verified` block as human-owned (§6.4) - the moment this
tool's first entry lands, a future rebuild is already protected from
overwriting it, and that protection was never conditional on *how* the block
got there.

Errors propagate uncaught - `LookupError` for an unknown concept, `LockHeld`
for a concurrent writer - because unlike `write.py`'s `add_source` there is no
prose already committed to a corpus that a traceback would wrongly disown:
nothing here has written anything until the whole call succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import okf, vectors
from ..config import Config
from ..lock import hold
from .query import resolve_concept_path


@dataclass
class VerifiedConcept:
    concept_id: str
    by: str
    at: str
    trust: str
    verified: list[dict[str, str]] = field(default_factory=list)


def verify_concept(config: Config, concept_id: str, identity: str) -> VerifiedConcept:
    """Appends `{by: "human:<identity>", at: <now>}` to the concept's `verified`
    list and returns the result. `identity` must be the caller's own
    OAuth-verified subject - never a caller-supplied string - so `by` always
    starts with `human:`, matching the check `vectors/graph.py::trust` already
    uses for the human-reviewed tier.
    """
    with hold(config.sidecar_root, "verify"):
        path = resolve_concept_path(config.bundle_root, concept_id)
        resolved_id = path.relative_to(config.bundle_root).as_posix().removesuffix(".md")
        concept = okf.parse(path.read_text(encoding="utf-8-sig"), resolved_id)
        entry = {"by": f"human:{identity}", "at": _now_utc_iso()}
        concept.verified = [*concept.verified, entry]
        okf.write_concept(config.bundle_root, concept)
        return VerifiedConcept(
            concept_id=resolved_id,
            by=entry["by"],
            at=entry["at"],
            trust=vectors.trust(concept),
            verified=concept.verified,
        )


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
