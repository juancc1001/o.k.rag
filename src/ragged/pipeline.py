"""M1 pipeline: discover -> extract -> synthesize -> emit -> validate.

Deliberately not here yet: segmentation (one concept per source file for now),
link-weave, per-directory index generation, the manifest, vectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import okf
from .config import Config
from .extract import ExtractionError, extract
from .okf import Concept, Source
from .sources import LocalFileSource, SourceItem
from .synth import MissingCredential, SynthError, resolve, synthesize


@dataclass
class ItemOutcome:
    uri: str
    concept_id: str | None = None
    error: str | None = None
    new_type: str | None = None


@dataclass
class IngestSummary:
    outcomes: list[ItemOutcome] = field(default_factory=list)
    report: okf.Report | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def written(self) -> list[ItemOutcome]:
        return [o for o in self.outcomes if o.concept_id]

    @property
    def failed(self) -> list[ItemOutcome]:
        return [o for o in self.outcomes if o.error]


def ingest(config: Config, dry_run: bool = False) -> IngestSummary:
    source = LocalFileSource(
        root=config.sources_root,
        include=config.local.include,
        exclude=config.local.exclude,
        project_root=config.project_root,
    )
    summary = IngestSummary()
    if not dry_run:
        # fail before extracting a whole corpus, not after
        impl = resolve(config.synth.provider)
        if not impl.credential_present():
            raise MissingCredential(
                f"no credential for provider {config.synth.provider!r}; "
                f"set one of {', '.join(impl.ENV_VARS)} in .env or the environment"
            )
    taken: set[str] = set()
    log_entries: list[str] = []
    index_entries: list[tuple[str, str]] = []

    try:
        for item in source.discover():
            outcome = ItemOutcome(uri=item.uri)
            summary.outcomes.append(outcome)
            try:
                text = extract(
                    config.project_root / item.uri,
                    item.content_sha256,
                    cache_dir=config.sidecar_root / "extracted",
                ).text
            except ExtractionError as exc:
                outcome.error = str(exc)
                continue

            if dry_run:
                outcome.concept_id = _claim_id(item.title, taken)
                continue

            source_id = okf.slugify(item.title) or "source"
            try:
                result = synthesize(
                    text=text,
                    source_id=source_id,
                    source_uri=item.uri,
                    source_title=item.title,
                    allowed_types=config.types.allowed,
                    provider=config.synth.provider,
                    model=config.synth.model,
                    effort=config.synth.effort,
                    max_tokens=config.synth.max_tokens,
                )
            except SynthError as exc:
                outcome.error = str(exc)
                continue

            summary.input_tokens += result.input_tokens
            summary.output_tokens += result.output_tokens
            summary.cache_read_tokens += result.cache_read_tokens

            if config.types.allowed and result.type not in config.types.allowed:
                outcome.new_type = result.type

            concept_id = _claim_id(item.title, taken)
            concept = _build(config, item, result, concept_id, source_id)
            okf.write_concept(config.bundle_root, concept)

            outcome.concept_id = concept_id
            index_entries.append((concept_id, result.description))
            log_entries.append(
                f"**Creation**: Added [{result.title}](/{concept_id}.md) from `{item.uri}`."
            )
    finally:
        # Concepts are written per file, but only the index, log and manifest make
        # them a bundle. Run on the way out however we leave — a Ctrl-C or an
        # unforeseen crash must not strand the model calls already paid for.
        if not dry_run and index_entries:
            _finalize(config, index_entries, log_entries)

    if config.bundle_root.exists():
        summary.report = okf.validate(config.bundle_root)
    return summary


def _finalize(
    config: Config,
    index_entries: list[tuple[str, str]],
    log_entries: list[str],
) -> None:
    title = config.project_root.name.replace("-", " ").replace("_", " ").title()
    okf.write_root_index(config.bundle_root, config.bundle.okf_version, title, index_entries)
    okf.append_log(
        config.bundle_root,
        datetime.now(timezone.utc).date().isoformat(),
        log_entries,
    )
    okf.write_manifest(config.project_root, config.bundle.root, title)


def _build(
    config: Config,
    item: SourceItem,
    result,
    concept_id: str,
    source_id: str,
) -> Concept:
    resource = (config.project_root / item.uri).resolve().as_uri()
    return Concept(
        concept_id=concept_id,
        type=result.type,
        title=result.title,
        description=result.description,
        body=result.body_markdown,
        resource=resource,
        tags=result.tags,
        sources=[
            Source(
                id=source_id,
                resource=resource,
                title=item.title,
                last_modified=item.mtime.date().isoformat(),
            )
        ],
        generated={
            "by": config.bundle.actor,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        status="draft",
        extra={"x_ragged_source_hash": item.content_sha256},
    )


def _claim_id(title: str, taken: set[str]) -> str:
    base = okf.slugify(title) or "concept"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    taken.add(candidate)
    return candidate


def inspect_extraction(config: Config, path: Path) -> str:
    import hashlib

    data = path.read_bytes()
    return extract(
        path,
        hashlib.sha256(data).hexdigest(),
        cache_dir=config.sidecar_root / "extracted",
    ).text
