"""The write half of the agent contract: `add_source` (DESIGN §10, §13 M5).

Knowledge worth recording is not always in a document — two tables are related in
a way no paper states, and someone knows it. This is how that enters the bundle,
and it enters as a *source*: the assertion is written into the corpus and the
pipeline authors the concept from it.

A tool that wrote a concept directly would be shorter and would break the
invariant everything else rests on — **every concept traces to a source** (§4.2).
It would either fabricate provenance or omit it, and it would appear in the
bundle while being absent from the ledger, so what the ledger describes and what
the bundle contains would stop agreeing — which is the assumption stale-output
cleanup, deprecation and human-edit detection are all built on.

The run is a partial one over the file just written, which suppresses the
vanished-source sweep (§6.1): safe by construction rather than by a check.

The project lock and the staged emit come with `ingest` (§6.6), so two of these at
once serialize on the bundle rather than on the source file — which needs no lock,
because the path is generated per assertion and identical bytes reuse it.

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
from ..pipeline import IngestSummary, ItemOutcome, ingest, require_credential
from ..sources import LocalFileSource

DIRECTORY = "conversations"
"""Where an asserted source lands, under the configured sources root. Named for
where the assertion came from — somebody said it — not for what it is about."""

SLUG_WORDS = 8
SLUG_MAX = 60


@dataclass
class AddedSource:
    uri: str
    """The source file, project-relative. Generated here, never supplied."""
    concepts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    """The bytes were already on disk, so the content-hash skip (§6.1) answered
    and no model was called."""


def add_source(config: Config, text: str, title: str | None = None) -> AddedSource:
    """Write prose into the corpus as a source, then ingest that path alone."""
    body = _normalized(text)
    if not body.strip():
        raise ValueError("add_source records prose; the text was empty")
    if okf.split(body)[0] is not None:
        raise ValueError(
            "add_source takes prose, not a concept: this text opens with a YAML "
            "frontmatter block. Submit what you know and let the pipeline author "
            "the concept, so it carries the provenance a concept requires."
        )
    # before writing anything, so a missing key leaves no source file behind
    require_credential(config)

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

    uri = path.relative_to(config.project_root).as_posix()
    summary = ingest(config, only=[uri])
    return AddedSource(
        uri=uri,
        concepts=[cid for outcome in summary.outcomes for cid in _produced(outcome)],
        errors=_errors(summary),
        skipped=bool(summary.skipped),
    )


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
