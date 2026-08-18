"""The write half of the agent contract: `add_source` (DESIGN §10, §13 M5, M6).

Knowledge worth recording is not always in a document — two tables are related in
a way no paper states, and someone knows it. This is how that enters the bundle,
and it enters as a *source*: the assertion is written into the corpus, and the
concept is authored from it.

A tool that wrote a concept directly would be shorter and would break the
invariant everything else rests on — **every concept traces to a source** (§4.2).
It would either fabricate provenance or omit it, and it would appear in the
bundle while being absent from the ledger, so what the ledger describes and what
the bundle contains would stop agreeing — which is the assumption stale-output
cleanup, deprecation and human-edit detection are all built on.

**Recording and synthesizing are separate, and which of them happens is a
property of the instance rather than of the call.** Without `synthesize` — what an
`okragged mcp --write` server offers — this writes the file and stops, and the
concept is authored by the next `okragged build` on the operator's machine with
the operator's key. With it, which only `--write --spend` reaches, the same call
ingests that one path and returns the concepts it authored.

**The guard on the synthesizing mode is that the operator launched the instance
with both switches, and it is nothing more than that.** There is no identity here
and no rate limit. A caller who owns the process could present any token he was
asked for and delete any counter kept beside the bundle, so the only thing the
switches establish is that whoever configured this server is the one paying. That
holds while the caller and the payer are the same person — a producer driving his
own agent — and stops holding the moment there is a channel with somebody else on
the far end, which is where an identity and a scope belong (§13 M8).

The run is a partial one over the file just written, which suppresses the
vanished-source sweep (§6.1): safe by construction rather than by a check.

**Nothing after the file is written may raise.** The prose is in the corpus by
then, and an exception crossing the wire would tell the caller their assertion was
lost while it sat in `sources/` waiting for a build that will pick it up. A
missing credential and a lock another writer holds are therefore reported in the
result. And the credential a *synthesis* needs is `[synth]`'s, which is not the
one `search` spends even where a single variable happens to hold both — so the
message names which is missing rather than saying a key is.

Writing the file needs no lock: the path is generated per assertion, and
identical bytes reuse the path they are already at rather than racing for it. The
lock and the staged emit arrive with `ingest` (§6.6), so two synthesizing calls
serialize on the bundle rather than on the source file.

Not imported by `agent/__init__.py`, for the same reason `server` is not: it
imports `pipeline`, which imports that package to regenerate the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import okf
from ..config import Config
from ..lock import LockHeld
from ..pipeline import IngestSummary, ItemOutcome, ingest, require_credential
from ..sources import LocalFileSource
from ..synth import MissingCredential

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
    errors: list[str] = field(default_factory=list)
    """Why no concept was authored, or what failed while one was. Never a reason
    the recording did not happen: that raises, having left nothing behind."""


def add_source(
    config: Config, text: str, title: str | None = None, synthesize: bool = False
) -> AddedSource:
    """Record prose in the corpus as a source, and author its concept if asked.

    Recording is offline and needs no credential. `synthesize` ingests the path
    just written, which costs a model call — the module docstring says what stands
    behind that and, more to the point, what does not.
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

    Identical bytes reuse the path they are already at: the ingest that follows
    then skips on the content hash, so re-asserting the same thing costs nothing.
    Different bytes under a name taken today get a suffix rather than overwriting
    a source somebody else's concepts already derive from.
    """
    directory = config.sources_root / DIRECTORY
    stem = f"{datetime.now(timezone.utc):%Y-%m-%d}-{slug}"
    for suffix in ("", *(f"-{n}" for n in range(2, 100))):
        path = _confined(directory, f"{stem}{suffix}.md")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            return path, True
        if path.is_file() and path.read_text(encoding="utf-8-sig") == content:
            return path, False
    raise ValueError(f"too many sources already named {stem!r} today")


def _confined(directory: Path, name: str) -> Path:
    """The mirror of `query._resolve` on the write side: the caller names no
    path, and the name derived from what it did say cannot leave this directory."""
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
