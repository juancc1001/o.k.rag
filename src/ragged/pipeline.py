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
from .synth import (
    MissingCredential,
    Outline,
    SectionContext,
    SynthError,
    SynthResult,
    needs_segmenting,
    plan,
    resolve,
    synthesize,
)


@dataclass
class ItemOutcome:
    uri: str
    concept_id: str | None = None
    error: str | None = None
    new_type: str | None = None
    child_ids: list[str] = field(default_factory=list)
    """Set when the source was segmented; `concept_id` is then the parent."""
    section_errors: list[str] = field(default_factory=list)
    """A section that failed does not fail the document."""
    dropped_sections: list[str] = field(default_factory=list)
    """Planned sections whose start_marker could not be found in the text."""

    @property
    def concept_count(self) -> int:
        return (1 if self.concept_id else 0) + len(self.child_ids)


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
            worker = _ingest_segmented if needs_segmenting(text) else _ingest_single
            try:
                entry, lines = worker(
                    config=config,
                    item=item,
                    text=text,
                    source_id=source_id,
                    outcome=outcome,
                    taken=taken,
                    summary=summary,
                )
            except SynthError as exc:
                outcome.error = str(exc)
                continue

            index_entries.append(entry)
            log_entries.extend(lines)
    finally:
        # Concepts are written per file, but only the index, log and manifest make
        # them a bundle. Run on the way out however we leave — a Ctrl-C or an
        # unforeseen crash must not strand the model calls already paid for.
        if not dry_run and index_entries:
            _finalize(config, index_entries, log_entries)

    if config.bundle_root.exists():
        summary.report = okf.validate(config.bundle_root)
    return summary


def _ingest_single(
    *,
    config: Config,
    item: SourceItem,
    text: str,
    source_id: str,
    outcome: ItemOutcome,
    taken: set[str],
    summary: IngestSummary,
) -> tuple[tuple[str, str], list[str]]:
    """Short enough to be one concept (§4.3). The M1 path, unchanged."""
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
    _account(summary, result)
    _note_new_type(config, outcome, result.type)

    concept_id = _claim_id(item.title, taken)
    okf.write_concept(config.bundle_root, _build(config, item, result, concept_id, source_id))
    outcome.concept_id = concept_id
    return (
        (concept_id, result.description),
        [f"**Creation**: Added [{result.title}](/{concept_id}.md) from `{item.uri}`."],
    )


def _ingest_segmented(
    *,
    config: Config,
    item: SourceItem,
    text: str,
    source_id: str,
    outcome: ItemOutcome,
    taken: set[str],
    summary: IngestSummary,
) -> tuple[tuple[str, str], list[str]]:
    """Plan first, then one call per section, then assemble the parent.

    A failed section is recorded and skipped: one refusal or one 429 must not
    discard the sections that did synthesize.
    """
    outline = plan(
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
    _account(summary, outline)
    _note_new_type(config, outcome, outline.type)
    outcome.dropped_sections = list(outline.unmatched)

    parent_id = _claim_id(outline.title or item.title, taken)
    children: list[tuple[str, str]] = []
    log_lines: list[str] = []
    total = len(outline.sections)

    for index, section in enumerate(outline.sections, start=1):
        try:
            result = synthesize(
                text=section.text,
                source_id=source_id,
                source_uri=item.uri,
                source_title=item.title,
                allowed_types=config.types.allowed,
                provider=config.synth.provider,
                model=config.synth.model,
                effort=config.synth.effort,
                max_tokens=config.synth.max_tokens,
                section=SectionContext(
                    document_title=outline.title,
                    document_overview=outline.overview,
                    section_title=section.title,
                    index=index,
                    total=total,
                ),
            )
        except SynthError as exc:
            outcome.section_errors.append(f"{section.title}: {exc}")
            continue

        _account(summary, result)
        _note_new_type(config, outcome, result.type)

        child_id = _claim_child_id(parent_id, result.title or section.title, taken)
        okf.write_concept(config.bundle_root, _build(config, item, result, child_id, source_id))
        outcome.child_ids.append(child_id)
        children.append((child_id.split("/", 1)[1], result.description))
        log_lines.append(
            f"**Creation**: Added [{result.title}](/{child_id}.md) from `{item.uri}`."
        )

    if not children:
        raise SynthError(
            f"all {total} section(s) failed: " + "; ".join(outcome.section_errors)
        )

    okf.write_concept(
        config.bundle_root,
        _build_parent(config, item, outline, parent_id, source_id, children),
    )
    okf.write_child_index(config.bundle_root, parent_id, outline.title, children)
    outcome.concept_id = parent_id
    log_lines.insert(
        0, f"**Creation**: Added [{outline.title}](/{parent_id}.md) from `{item.uri}`."
    )
    return (parent_id, outline.description), log_lines


def _account(summary: IngestSummary, result) -> None:
    summary.input_tokens += result.input_tokens
    summary.output_tokens += result.output_tokens
    summary.cache_read_tokens += result.cache_read_tokens


def _note_new_type(config: Config, outcome: ItemOutcome, type_name: str) -> None:
    if config.types.allowed and type_name not in config.types.allowed:
        outcome.new_type = type_name


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


def _build_parent(
    config: Config,
    item: SourceItem,
    outline: Outline,
    parent_id: str,
    source_id: str,
    children: list[tuple[str, str]],
) -> Concept:
    """The parent's body is the outline's overview plus the way down to the children.

    §4.3 also wants each child to link back in prose. That is link-weave (M2 #3),
    still pending; fabricating the sentence here would be inventing content.
    """
    lines = [outline.overview.strip(), "", "# Sections", ""]
    for slug, description in children:
        label = slug.rsplit("/", 1)[-1].replace("-", " ").capitalize()
        suffix = f" - {description}" if description else ""
        lines.append(f"- [{label}](/{parent_id}/{slug}.md){suffix}")
    # no footnote: `sources` carries provenance, and footnotes are for per-claim
    # attribution (§5.1) — the overview plus a link list makes no specific claim
    body = "\n".join(lines).rstrip() + "\n"

    result = SynthResult(
        type=outline.type,
        title=outline.title,
        description=outline.description,
        body_markdown=body,
        tags=outline.tags,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
    )
    return _build(config, item, result, parent_id, source_id)


def _claim_id(title: str, taken: set[str]) -> str:
    base = okf.slugify(title) or "concept"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    taken.add(candidate)
    return candidate


def _claim_child_id(parent_id: str, title: str, taken: set[str]) -> str:
    leaf = okf.slugify(title) or "section"
    candidate, n = f"{parent_id}/{leaf}", 2
    while candidate in taken:
        candidate, n = f"{parent_id}/{leaf}-{n}", n + 1
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
