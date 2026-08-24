"""The write half of the agent contract: `add_source` (DESIGN.md §10).

Knowledge worth recording isn't always in a document — two tables are
related in a way no paper states, and someone knows it. This is how that
enters the bundle, as a *source*: the assertion is written into the corpus,
and the concept is authored from it.

A tool that wrote a concept directly would be shorter, but it would break the
invariant everything else rests on: **every concept traces to a source**
(DESIGN.md §4.2). It would either fabricate provenance or omit it, appearing
in the bundle while absent from the ledger — breaking the assumption that
stale-output cleanup, deprecation, and human-edit detection are all built on.

**Recording and synthesizing are separate, and which one happens is a
property of the instance, not of the call.** Without `synthesize` — what an
`okragged mcp --write` server offers — this writes the file and stops; the
concept is authored later by `okragged build`, on the operator's machine
with the operator's key. With it, reachable only via `--write --spend`, the
same call ingests that one path and returns the concepts it authored.

**The guard on the synthesizing mode is only that the operator launched the
instance with both switches — nothing more, and that is still true here.**
This module has no identity and no rate limit, and never will: a caller who
owns the process could present any token it's asked for and delete any
counter kept beside the bundle, so neither would mean anything at this layer.
Under stdio that is the whole guard, and it holds exactly while the caller and
the payer are the same person — a producer driving their own agent.

**Under `--transport http` the gate lives one layer up, in `agent/server.py`
and `agent/auth.py`, not here** (DESIGN.md §13 M8, criterion 4): the caller's
identity is a verified JWT claim `write.py` never sees, `authorize_spend`
checks it against `[mcp].spend_scopes` and a `SpendRateLimiter` before this
module's `add_source` is ever called, and a refused call never reaches this
function at all. This module stays what it always was — recording and
synthesizing, not attributing a call to whoever made it.

The run is a partial one over the file just written, which safely skips the
vanished-source sweep by construction rather than by a check (that sweep
only runs over a full scan, DESIGN.md §6.1).

**A synthesizing call also refreshes the vectors, as the same decision rather
than a second one.** `--spend` says the operator's credential may be used,
and a concept nobody can find isn't worth what the synthesis cost. Without
this refresh, the call would author a concept that `search`'s embedding path
can't rank until a human separately runs `okragged index` — the write path
and the read path on different clocks. The refresh reuses any vector whose
body hash is unchanged, so it costs one embedding call, and the graph is
regenerated locally since `lint` and the view both read it. `index` takes the
project lock itself, and locks aren't reentrant, so this runs after `ingest`
has released it, never inside.

**Nothing after the file is written may raise.** The prose is already in the
corpus by then, so an exception crossing the wire would wrongly tell the
caller their assertion was lost. A missing credential or another writer's
lock are reported in the result instead — and since synthesis needs the
`[synth]` credential while `search` spends `[vectors]`'s, the message names
which one is missing rather than just saying "no credential".

Writing the file itself needs no lock: the path is generated per assertion,
and identical bytes reuse the path they're already at rather than racing for
it. The lock and the staged emit arrive with `ingest` (DESIGN.md §6.6), so
two synthesizing calls serialize on the bundle, not on the source file.

Not imported by `agent/__init__.py`, for the same reason `server` is not: it
imports `pipeline`, which imports that package to regenerate the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import okf, vectors
from ..config import Config
from ..lock import LockHeld
from ..pipeline import IngestSummary, ItemOutcome, ingest, require_credential
from ..sources import LocalFileSource
from ..synth import MissingCredential
from ..vectors import MissingEmbedCredential

DIRECTORY = "conversations"
"""Where an asserted source lands, under the configured sources root. Named for
where the assertion came from — somebody said it — not for what it is about."""

SLUG_WORDS = 8
SLUG_MAX = 60


@dataclass
class AddedSource:
    uri: str
    """The source file, project-relative. Generated here, never supplied."""
    recorded: bool = True
    """False when identical bytes were already at this path, so nothing was
    written. The assertion is in the corpus either way — re-asserting the same
    thing is a no-op rather than a second source saying it twice."""
    synthesized: bool = False
    """Whether this call authored a concept. False when the instance only records,
    and false when it was asked to and could not — the prose is recorded either
    way and the next `okragged build` authors from it, which is why the failure is
    reported here rather than raised."""
    concepts: list[str] = field(default_factory=list)
    """The concept ids this call authored, parent and children, when it did."""
    indexed: bool = False
    """Whether `search`'s embedding path can now rank what this call authored.
    False when nothing was authored, and false when the vectors could not be
    refreshed — the concept is in the bundle and readable either way, but until
    `okragged index` runs it is rankable only by traversal, which `search` says."""
    errors: list[str] = field(default_factory=list)
    """Why no concept was authored, or what failed while one was — the vectors
    included. Never a reason the recording did not happen: that raises, having
    left nothing behind."""


def add_source(
    config: Config, text: str, title: str | None = None, synthesize: bool = False
) -> AddedSource:
    """Record prose in the corpus as a source, and author its concept if asked.

    Recording is offline and needs no credential. `synthesize` ingests the path
    just written and refreshes the vectors over it, which costs a synthesis call
    and one embedding — the module docstring says what stands behind that and,
    more to the point, what does not.
    """
    body = _normalized(text)
    if not body.strip():
        raise ValueError("add_source records prose; the text was empty")
    if okf.split(body)[0] is not None:
        raise ValueError(
            "add_source takes prose, not a concept: this text opens with a YAML "
            "frontmatter block. Submit what you know and let the pipeline author "
            "the concept, so it carries the provenance a concept requires."
        )

    source = LocalFileSource(
        root=config.sources_root,
        include=config.local.include,
        exclude=config.local.exclude,
        project_root=config.project_root,
    )
    content = body.strip() + "\n"
    path, wrote = _write(config, _slug(title or body), content)
    if not source.matches(path):
        if wrote:
            path.unlink()
        raise ValueError(
            f"the source would land in {DIRECTORY}/, which "
            f"[sources.local] include = {config.local.include} does not match, so "
            "the pipeline would never discover it"
        )

    recorded = AddedSource(
        uri=path.relative_to(config.project_root).as_posix(), recorded=wrote
    )
    return _synthesize(config, recorded) if synthesize else recorded


def _synthesize(config: Config, recorded: AddedSource) -> AddedSource:
    """Ingest the one path just written, reporting each failure as a result.

    The source file exists by the time this runs, so nothing here raises: the
    assertion is in the corpus and a later `okragged build` authors from it, which
    is the outcome to describe rather than to hide behind a traceback.
    """
    try:
        # asked apart from the call so the answer can name *which* credential:
        # `[synth]` pays for this and `[vectors]` pays for search, and the gemini
        # modules read one variable for both, so a working search implies nothing
        require_credential(config)
    except MissingCredential as exc:
        return _unauthored(recorded, str(exc))
    try:
        summary = ingest(config, only=[recorded.uri])
    except LockHeld as exc:
        return _unauthored(recorded, str(exc))
    except Exception as exc:  # the source is already written; report, never raise
        return _unauthored(recorded, f"{type(exc).__name__}: {exc}")

    recorded.synthesized = True
    recorded.concepts = [cid for outcome in summary.outcomes for cid in _produced(outcome)]
    recorded.errors = _errors(summary)
    return _reindex(config, recorded)


def _reindex(config: Config, recorded: AddedSource) -> AddedSource:
    """Refresh the vectors so `search` can rank the concept this call authored.

    Outside `ingest`'s lock rather than inside it: `index` takes the project lock
    itself and `hold` is not reentrant, so nesting them would self-refuse. That
    leaves a window another writer can take the lock in, which is reported like
    any other failure here — the concept is authored and readable regardless.
    """
    if not recorded.concepts:
        return recorded
    try:
        summary = vectors.index(config)
    except MissingEmbedCredential as exc:
        return _unranked(recorded, str(exc))
    except LockHeld as exc:
        return _unranked(recorded, str(exc))
    except Exception as exc:  # the concept is already published; report, never raise
        return _unranked(recorded, f"{type(exc).__name__}: {exc}")

    failed = [summary.embed.errors[cid] for cid in recorded.concepts if cid in summary.embed.errors]
    if failed:
        return _unranked(recorded, failed[0])
    recorded.indexed = True
    return recorded


def _unranked(recorded: AddedSource, reason: str) -> AddedSource:
    """The concept landed and its vector did not. Said in full, because a caller
    told only the reason would read it as the concept having failed."""
    recorded.errors.append(
        f"the concept is in the bundle and readable, but has no vector, so "
        f"`search` ranks it by index traversal until `okragged index` runs: {reason}"
    )
    return recorded


def _unauthored(recorded: AddedSource, reason: str) -> AddedSource:
    """The prose landed and the concept did not, said in one sentence — a caller
    told only the reason would not know their assertion is safe."""
    recorded.errors.append(
        f"the prose is recorded at {recorded.uri} and the next `okragged build` "
        f"will author its concept, but this call did not: {reason}"
    )
    return recorded


def _write(config: Config, slug: str, content: str) -> tuple[Path, bool]:
    """The generated path, and whether this call wrote it.

    Claims a name with `open(path, "x", ...)` — the same exclusive-create
    `lock.py::_claim` uses — rather than an `exists()` check followed by a
    separate write: two concurrent callers can both see a name free between
    those two steps, and the second `write_text()` would silently overwrite the
    first's. Exclusive create fails atomically instead, so the loser falls
    through to the content-match check below rather than clobbering the winner.

    Identical bytes reuse the path they are already at: the ingest that follows
    then skips on the content hash, so re-asserting the same thing costs nothing.
    Different bytes under a name taken today get a suffix rather than overwriting
    a source somebody else's concepts already derive from.
    """
    directory = config.sources_root / DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now(timezone.utc):%Y-%m-%d}-{slug}"
    for suffix in ("", *(f"-{n}" for n in range(2, 100))):
        path = _confined(directory, f"{stem}{suffix}.md")
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            return path, True
        except FileExistsError:
            if path.is_file() and path.read_text(encoding="utf-8-sig") == content:
                return path, False
    raise ValueError(f"too many sources already named {stem!r} today")


def _confined(directory: Path, name: str) -> Path:
    """The mirror of `query.resolve_concept_path` on the write side: the caller
    names no path, and the name derived from what it did say cannot leave this
    directory."""
    path = directory / name
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"refusing to write outside {DIRECTORY}/")
    return path


def _slug(seed: str) -> str:
    first = next((line for line in seed.splitlines() if line.strip()), "")
    words = re.sub(r"[^\w\s-]+", " ", first, flags=re.UNICODE).split()[:SLUG_WORDS]
    # `/` would nest, which slugify preserves on purpose for concept ids
    slug = okf.slugify(" ".join(words).replace("/", " "))
    return slug[:SLUG_MAX].strip("-.") or "note"


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _produced(outcome: ItemOutcome) -> list[str]:
    return [outcome.concept_id, *outcome.child_ids] if outcome.concept_id else []


def _errors(summary: IngestSummary) -> list[str]:
    errors = [f"{o.uri}: {o.error}" for o in summary.failed]
    for outcome in summary.outcomes:
        errors += outcome.section_errors + outcome.weave_errors
    return errors
