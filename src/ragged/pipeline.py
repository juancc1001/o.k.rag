"""The pipeline: discover -> extract -> plan -> synthesize -> link-weave -> emit
-> validate.

Deliberately not here yet: general per-directory index generation, vectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import okf
from .config import Config
from .extract import ExtractionError, extract
from .ledger import LEDGER_NAME, Ledger
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
    weave,
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
    skipped: bool = False
    """Unchanged since the last ingest — reused wholesale, no model call."""
    protected: dict[str, str] = field(default_factory=dict)
    """concept_id -> why a human owns it; the replacement went to .ragged/proposed/."""
    removed: list[str] = field(default_factory=list)
    """Concepts the previous run produced for this source and this one did not."""
    candidates: dict[str, list[str]] = field(default_factory=dict)
    """concept_id -> ids synthesis thought it relates to. Link-weave's hint."""
    linked: list[str] = field(default_factory=list)
    """Concepts the weave pass actually rewrote."""
    weave_errors: list[str] = field(default_factory=list)
    """A concept that failed to weave keeps its unlinked body."""

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
    deprecated: list[str] = field(default_factory=list)
    """Concepts whose source is gone. Marked, not deleted (§6.1)."""
    invented_links: list[str] = field(default_factory=list)
    """Weave targets outside the bundle. Unwrapped to plain text, never silent."""

    @property
    def written(self) -> list[ItemOutcome]:
        return [o for o in self.outcomes if o.concept_id]

    @property
    def failed(self) -> list[ItemOutcome]:
        return [o for o in self.outcomes if o.error]

    @property
    def skipped(self) -> list[ItemOutcome]:
        return [o for o in self.outcomes if o.skipped]

    @property
    def synthesized(self) -> list[ItemOutcome]:
        return [o for o in self.outcomes if o.concept_id and not o.skipped]


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
    ledger_path = config.sidecar_root / LEDGER_NAME
    ledger = Ledger() if dry_run else Ledger.load(ledger_path)
    # snapshotted once: the synthesis prefix has to stay byte-identical all run
    existing = [] if dry_run else _index(config.bundle_root)
    # every id the last run wrote stays spoken for until its own source releases
    # it, so a source that is skipped this run cannot have its ids stolen
    taken: set[str] = set(ledger.concepts)
    log_entries: list[str] = []
    discovered: set[str] = set()

    try:
        for item in source.discover():
            outcome = ItemOutcome(uri=item.uri)
            summary.outcomes.append(outcome)
            discovered.add(item.uri)

            record = ledger.unchanged(item.uri, item.content_sha256, config.bundle_root)
            if record is not None:
                if _parses(config.bundle_root, record.concepts[0]):
                    outcome.skipped = True
                    outcome.concept_id = record.concepts[0]
                    outcome.child_ids = list(record.concepts[1:])
                    continue
                # its parent no longer reads; rebuild rather than trust the ledger

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

            prior = ledger.sources.get(item.uri)
            previous = set(prior.concepts) if prior else set()
            taken -= previous  # a re-ingested source may reuse its own ids

            source_id = okf.slugify(item.title) or "source"
            worker = _ingest_segmented if needs_segmenting(text) else _ingest_single
            try:
                lines = worker(
                    config=config,
                    ledger=ledger,
                    item=item,
                    text=text,
                    source_id=source_id,
                    outcome=outcome,
                    taken=taken,
                    summary=summary,
                    existing=existing,
                )
            except SynthError as exc:
                outcome.error = str(exc)
                taken |= previous  # the last run's concepts are still on disk
                continue

            produced = [outcome.concept_id, *outcome.child_ids]
            ledger.note_source(
                item.uri,
                content_sha256=item.content_sha256,
                size_bytes=item.size_bytes,
                mtime=item.mtime.isoformat(timespec="seconds").replace("+00:00", "Z"),
                concepts=produced,
                at=_now(),
                # a section lost to a 429 is source content missing from the
                # bundle, so the next run redoes this file rather than skipping
                # it and losing that section until the bytes happen to change
                complete=not outcome.section_errors,
            )
            outcome.removed = _remove_stale(config, ledger, previous - set(produced))
            log_entries.extend(
                f"**Deprecation**: Removed `{cid}`, no longer produced by `{item.uri}`."
                for cid in outcome.removed
            )
            log_entries.extend(lines)

        if not dry_run:
            # after every concept for the run exists: a link can only point at
            # a concept that has been written
            _weave_run(config, ledger, summary)
            _deprecate_vanished(config, ledger, discovered, summary, log_entries)
    finally:
        # Concepts are written per file, but only the indexes, log and ledger
        # make them a bundle. Run on the way out however we leave — a Ctrl-C or
        # an unforeseen crash must not strand the model calls already paid for.
        if not dry_run and config.bundle_root.exists():
            _finalize(config, log_entries, ledger, ledger_path)

    if config.bundle_root.exists():
        summary.report = okf.validate(config.bundle_root)
    return summary


def _ingest_single(
    *,
    config: Config,
    ledger: Ledger,
    item: SourceItem,
    text: str,
    source_id: str,
    outcome: ItemOutcome,
    taken: set[str],
    summary: IngestSummary,
    existing: list[tuple[str, str]],
) -> list[str]:
    """Short enough to be one concept (§4.3). Returns its log lines."""
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
        index=existing,
    )
    _account(summary, result)
    _note_new_type(config, outcome, result.type)

    concept_id = _claim_id(item.title, taken)
    outcome.candidates[concept_id] = result.candidate_links
    verb = _write(config, ledger, _build(config, item, result, concept_id, source_id), outcome)
    outcome.concept_id = concept_id
    return _log_lines(verb, result.title, concept_id, item.uri)


def _ingest_segmented(
    *,
    config: Config,
    ledger: Ledger,
    item: SourceItem,
    text: str,
    source_id: str,
    outcome: ItemOutcome,
    taken: set[str],
    summary: IngestSummary,
    existing: list[tuple[str, str]],
) -> list[str]:
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
    children: list[tuple[str, str, str]] = []
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
                index=existing,
            )
        except SynthError as exc:
            outcome.section_errors.append(f"{section.title}: {exc}")
            continue

        _account(summary, result)
        _note_new_type(config, outcome, result.type)

        child_id = _claim_child_id(parent_id, result.title or section.title, taken)
        outcome.candidates[child_id] = result.candidate_links
        verb = _write(config, ledger, _build(config, item, result, child_id, source_id), outcome)
        # recorded even when a human owns it: the file is still this source's
        # output, and dropping it here would delete it as stale
        outcome.child_ids.append(child_id)
        children.append((child_id.split("/", 1)[1], result.title, result.description))
        log_lines.extend(_log_lines(verb, result.title, child_id, item.uri))

    if not children:
        raise SynthError(
            f"all {total} section(s) failed: " + "; ".join(outcome.section_errors)
        )

    verb = _write(
        config,
        ledger,
        _build_parent(config, item, outline, parent_id, source_id, children),
        outcome,
    )
    outcome.concept_id = parent_id
    log_lines[:0] = _log_lines(verb, outline.title, parent_id, item.uri)
    return log_lines


def _write(config: Config, ledger: Ledger, concept: Concept, outcome: ItemOutcome) -> str:
    """Write a concept unless a human owns it (§6.4). Returns the log verb.

    A protected concept's replacement goes to `.ragged/proposed/` for the human
    to resolve. This is the one place where "the LLM owns layer 2" yields.
    """
    concept_id = concept.concept_id
    why = ledger.protection(config.bundle_root, concept_id)
    if why is not None:
        path = config.sidecar_root / "proposed" / f"{concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(okf.render(concept), encoding="utf-8", newline="\n")
        outcome.protected[concept_id] = why
        return ""
    existed = (config.bundle_root / f"{concept_id}.md").exists()
    okf.write_concept(config.bundle_root, concept)
    ledger.note_concept(concept_id, outcome.uri, concept.body)
    return "Update" if existed else "Creation"


def _log_lines(verb: str, title: str, concept_id: str, uri: str) -> list[str]:
    if not verb:
        return []  # nothing changed on disk; the human's copy still stands
    word = "Added" if verb == "Creation" else "Rewrote"
    return [f"**{verb}**: {word} [{title}](/{concept_id}.md) from `{uri}`."]


def _index(bundle_root: Path) -> list[tuple[str, str]]:
    """The bundle's concepts as `(id, description)`, for a prompt prefix."""
    return [(ref.concept_id, ref.description) for ref in okf.inventory(bundle_root)]


def _weave_run(config: Config, ledger: Ledger, summary: IngestSummary) -> None:
    """Rewrite this run's concepts with cross-links woven into their prose (§6.5).

    Only what this run wrote: a concept that was skipped is unchanged output and
    rewriting it would cost a model call to produce a file nobody asked for. The
    consequence is that links point forward from new concepts to old ones and not
    the other way round until the old one is itself re-ingested.
    """
    # titles as well as descriptions: the pass has to choose link labels, and
    # prune_links needs a title when the model puts a raw path in the label
    index = [
        (ref.concept_id, ref.title, ref.description) for ref in okf.inventory(config.bundle_root)
    ]
    if len(index) < 2:
        return  # nothing to link to

    for outcome in summary.synthesized:
        for concept_id in [outcome.concept_id, *outcome.child_ids]:
            if concept_id in outcome.protected:
                continue
            path = config.bundle_root / f"{concept_id}.md"
            try:
                concept = okf.parse(path.read_text(encoding="utf-8-sig"), concept_id)
            except (OSError, ValueError):
                continue
            try:
                result = weave(
                    concept_id=concept_id,
                    title=concept.title or concept_id,
                    body=concept.body,
                    index=index,
                    parent=concept_id.rsplit("/", 1)[0] if "/" in concept_id else None,
                    candidates=outcome.candidates.get(concept_id, []),
                    provider=config.synth.provider,
                    model=config.synth.model,
                    effort=config.synth.effort,
                    max_tokens=config.synth.max_tokens,
                )
            except SynthError as exc:
                # the unwoven concept is still a concept; weaving is an upgrade
                outcome.weave_errors.append(f"{concept_id}: {exc}")
                continue

            _account(summary, result)
            summary.invented_links.extend(result.invented)
            if result.body_markdown == concept.body:
                continue
            concept.body = result.body_markdown
            okf.write_concept(config.bundle_root, concept)
            # the post-weave hash, or the next run reads every concept as edited
            ledger.note_concept(concept_id, outcome.uri, concept.body)
            outcome.linked.append(concept_id)


def _remove_stale(config: Config, ledger: Ledger, stale: set[str]) -> list[str]:
    """Concepts the previous run produced for this source and this one did not.

    Ids come from model-written titles, so a rebuild renames the tree as a matter
    of course; without this nothing ever removes what the rename left behind.
    """
    removed: list[str] = []
    for concept_id in sorted(stale):
        if ledger.protection(config.bundle_root, concept_id) is not None:
            continue  # a human owns it now; orphaned beats deleted
        if okf.delete_concept(config.bundle_root, concept_id):
            removed.append(concept_id)
            ledger.forget_concept(concept_id)
    return removed


def _deprecate_vanished(
    config: Config,
    ledger: Ledger,
    discovered: set[str],
    summary: IngestSummary,
    log_entries: list[str],
) -> None:
    """A source that is gone deprecates its concepts; it does not delete them.

    The spec keeps deprecated concepts for links and history (§6.1) — deletion
    is an explicit `ragged prune`, never a side effect of removing an input file.
    """
    for uri in sorted(set(ledger.sources) - discovered):
        for concept_id in ledger.forget_source(uri):
            if okf.set_status(config.bundle_root, concept_id, "deprecated"):
                summary.deprecated.append(concept_id)
                log_entries.append(
                    f"**Deprecation**: `{uri}` is gone; "
                    f"[{concept_id}](/{concept_id}.md) marked deprecated."
                )


def _parses(bundle_root: Path, concept_id: str) -> bool:
    """A concept whose frontmatter no longer reads is rebuilt, not skipped."""
    try:
        okf.parse((bundle_root / f"{concept_id}.md").read_text(encoding="utf-8-sig"), concept_id)
    except (OSError, ValueError):
        return False
    return True


def _account(summary: IngestSummary, result) -> None:
    summary.input_tokens += result.input_tokens
    summary.output_tokens += result.output_tokens
    summary.cache_read_tokens += result.cache_read_tokens


def _note_new_type(config: Config, outcome: ItemOutcome, type_name: str) -> None:
    if config.types.allowed and type_name not in config.types.allowed:
        outcome.new_type = type_name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _finalize(
    config: Config,
    log_entries: list[str],
    ledger: Ledger,
    ledger_path: Path,
) -> None:
    title = config.bundle_title
    okf.write_indexes(config.bundle_root, config.bundle.okf_version, title)
    if log_entries:
        # a run that changed nothing must not leave an empty date heading behind
        okf.append_log(
            config.bundle_root,
            datetime.now(timezone.utc).date().isoformat(),
            log_entries,
        )
    okf.write_manifest(config.project_root, config.bundle.root, title)
    # last: the ledger is only true once every file it describes has landed
    ledger.bundle_root = config.bundle.root
    ledger.save(ledger_path)


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
        generated={"by": config.bundle.actor, "at": _now()},
        status="draft",
        extra={"x_ragged_source_hash": item.content_sha256},
    )


def _build_parent(
    config: Config,
    item: SourceItem,
    outline: Outline,
    parent_id: str,
    source_id: str,
    children: list[tuple[str, str, str]],
) -> Concept:
    """The parent's body is the outline's overview plus the way down to the children.

    §4.3's "each child links back to its parent in prose" is the other direction
    and belongs to link-weave (§6.5), which is told the parent — fabricating the
    sentence here would be inventing content.
    """
    lines = [outline.overview.strip(), "", "# Sections", ""]
    for slug, title, description in children:
        suffix = f" - {description}" if description else ""
        lines.append(f"- [{title}](/{parent_id}/{slug}.md){suffix}")
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
