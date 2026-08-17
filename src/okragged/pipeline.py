"""The pipeline: discover -> extract -> plan -> synthesize -> link-weave -> emit
-> validate.

Deliberately not here yet: general per-directory index generation, vectors.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import lock, okf, staging
from .agent import write_contract
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


DISCOVER = "discover"
EXTRACT = "extract"
PLAN = "plan"
SYNTHESIZE = "synthesize"
WEAVE = "weave"
SOURCE = "source"
"""One source is finished, however it finished. `detail` says how."""
FINALIZE = "finalize"
VALIDATE = "validate"

STAGES = (DISCOVER, EXTRACT, PLAN, SYNTHESIZE, WEAVE, SOURCE, FINALIZE, VALIDATE)
"""Every `stage` a `Progress` event can carry. Adding one is a compatible change;
removing or renaming one is not, because a caller renders these."""


@dataclass(frozen=True)
class Progress:
    """One event from a run in flight, for a caller that cannot wait for the summary.

    Frozen and flat on purpose. `ingest` reported only on completion, which is what
    deferred `POST /api/ingest` and its SSE stream (§11), and an event that has to
    survive being serialized to a browser is the wrong place for a live object
    graph. `unit` plus `index`/`total` identify what is happening; `detail` is a
    short note for a human to read and not for a caller to parse.
    """

    stage: str
    unit: str = ""
    """The source uri, or the concept id at the weave stage. Empty for a stage
    that is about the run rather than about one unit."""
    index: int = 0
    total: int = 0
    """1-based position and how many there are. `total` is 0 where the count is
    not yet known — discovery streams, so the source count is one of those."""
    detail: str = ""


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
    """concept_id -> why a human owns it; the replacement went to .okragged/proposed/."""
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
    recovered: list[str] = field(default_factory=list)
    """What this run found a previous one had left behind, and what it did about
    it (§6.6). Reported rather than repaired quietly: it says a build died."""

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


def require_credential(config: Config) -> None:
    """Raise before anything is spent or written. Callers with something to undo
    (`agent.write.add_source` writes a source file) ask first."""
    impl = resolve(config.synth.provider)
    if not impl.credential_present():
        raise MissingCredential(
            f"no credential for provider {config.synth.provider!r}; "
            f"set one of {', '.join(impl.ENV_VARS)} in .env or the environment"
        )


def ingest(
    config: Config,
    dry_run: bool = False,
    only: list[str] | None = None,
    contract: bool = True,
    progress: Callable[[Progress], None] | None = None,
) -> IngestSummary:
    """Run the pipeline over `config`'s sources.

    `contract` regenerates layer 3 (`agent/AGENTS.md`, `.mcp.json`) after the emit
    and it defaults to on because that is what `okragged build` must do: §10 relies
    on the contract never describing a bundle that no longer exists, and a CLI run
    is the moment the bundle changed. The public `okragged.build()` defaults it
    *off* for the opposite reason — a library call that drops `.mcp.json` into the
    caller's repository is a surprise, and `okragged.write_contract()` is there for
    a caller who wants it.

    `progress` is called as the run moves through its stages; see `Progress`.

    A real run **holds the project lock and stages each source's emit**, and
    **the commit boundary is the source, not the run** (§6.6, `staging.py`).
    Three things follow. A second writer is refused rather than interleaved. An
    interrupted run leaves every source it finished published *and* recorded, so
    the next one pays only for the remainder — the two-namings hazard lives inside
    one source's emit, and across sources a partial run is a valid bundle. And the
    one source that was in flight stays in the sidecar, unpublished, until the next
    run reports and clears it.
    """
    if dry_run:
        # no lock and no staging: a dry run writes no bundle and no ledger, so
        # there is nothing to serialize, and refusing one because a build is in
        # flight would put a new failure mode in the command whose whole point is
        # that it is safe to run at any time
        swept = _run(config, dry_run=True, only=only, progress=progress)
        return _validated(config, swept, progress)

    with lock.hold(config.sidecar_root, "ingest") as held:
        # before the credential check: a commit some earlier run left half applied
        # is finished even by a run that then cannot proceed for its own reasons
        notes = [note for note in (held.note, staging.reconcile(config)) if note]
        require_credential(config)  # before extracting a whole corpus, not after
        area = staging.prepare(config)
        summary = _run(config, dry_run=False, only=only, area=area, progress=progress)
        summary.recovered = notes
        staging.discard(area)  # a run that reached here staged nothing it did not commit
        if contract and config.bundle_root.exists():
            # after the commits and never before: layer 3 describes layer 2, and a
            # contract written beside concepts that did not land would describe
            # ones that do not exist (§10). A run that dies leaves the previous
            # contract, which under-describes the bundle until the next run — the
            # same self-healing direction as the indexes
            write_contract(config)
        return _validated(config, summary, progress)


def _validated(
    config: Config,
    summary: IngestSummary,
    progress: Callable[[Progress], None] | None,
) -> IngestSummary:
    """Validate the published bundle — after every commit, so the report is about
    the tree a reader will actually find."""
    if config.bundle_root.exists():
        _report(progress, Progress(VALIDATE))
        summary.report = okf.validate(config.bundle_root)
    return summary


def _run(
    config: Config,
    *,
    dry_run: bool,
    only: list[str] | None,
    progress: Callable[[Progress], None] | None,
    area: staging.Area | None = None,
) -> IngestSummary:
    """The stages themselves.

    Every *read* is of the published bundle, `config.bundle_root`: what is on disk
    is what previous sources and previous runs left there. Every *write* goes into
    `area`, and one commit per source publishes it (§6.6). The two roots are the
    whole of what "staged" means here — there is no copy of the bundle to keep in
    step with the real one.
    """
    selected = _selected(config, only)
    source = LocalFileSource(
        root=config.sources_root,
        include=config.local.include,
        exclude=config.local.exclude,
        project_root=config.project_root,
    )
    summary = IngestSummary()
    # loaded from the published ledger and saved only by a commit: a record of
    # this run is only true once the files it describes have landed
    ledger = Ledger() if dry_run else Ledger.load(config.sidecar_root / LEDGER_NAME)
    ledger.bundle_root = config.bundle.root
    # snapshotted once: the synthesis prefix has to stay byte-identical all run
    existing = [] if dry_run else _index(config.bundle_root)
    # every id the last run wrote stays spoken for until its own source releases
    # it, so a source that is skipped this run cannot have its ids stolen
    taken: set[str] = set(ledger.concepts)
    log_entries: list[str] = []
    discovered: set[str] = set()

    try:
        for item in source.discover():
            if selected is not None and item.uri not in selected:
                continue
            outcome = ItemOutcome(uri=item.uri)
            summary.outcomes.append(outcome)
            discovered.add(item.uri)
            _report(progress, Progress(DISCOVER, item.uri, index=len(summary.outcomes)))

            record = ledger.unchanged(item.uri, item.content_sha256, config.bundle_root)
            if record is not None:
                if _parses(config.bundle_root, record.concepts[0]):
                    outcome.skipped = True
                    outcome.concept_id = record.concepts[0]
                    outcome.child_ids = list(record.concepts[1:])
                    _report(progress, Progress(SOURCE, item.uri, detail="unchanged"))
                    continue
                # its parent no longer reads; rebuild rather than trust the ledger

            _report(progress, Progress(EXTRACT, item.uri))
            try:
                text = extract(
                    config.project_root / item.uri,
                    item.content_sha256,
                    cache_dir=config.sidecar_root / "extracted",
                ).text
            except ExtractionError as exc:
                outcome.error = str(exc)
                _report(progress, Progress(SOURCE, item.uri, detail=f"failed: {exc}"))
                continue

            if dry_run:
                outcome.concept_id = _claim_id(item.title, taken)
                _report(progress, Progress(SOURCE, item.uri, detail="extracted"))
                continue

            prior = ledger.sources.get(item.uri)
            previous = set(prior.concepts) if prior else set()
            taken -= previous  # a re-ingested source may reuse its own ids

            source_id = okf.slugify(item.title) or "source"
            worker = _ingest_segmented if needs_segmenting(text) else _ingest_single
            assert area is not None  # only a dry run has none, and it continued above
            staging.discard(area)  # whatever a previous unit left is not ours to place
            try:
                lines = worker(
                    config=config,
                    area=area,
                    ledger=ledger,
                    item=item,
                    text=text,
                    source_id=source_id,
                    outcome=outcome,
                    taken=taken,
                    summary=summary,
                    existing=existing,
                    progress=progress,
                )
            except SynthError as exc:
                outcome.error = str(exc)
                taken |= previous  # the last run's concepts are still on disk
                _report(progress, Progress(SOURCE, item.uri, detail=f"failed: {exc}"))
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
            outcome.removed = _stale(config, ledger, previous - set(produced))
            # the source is the commit boundary (§6.6): the concepts it wrote under
            # their new ids, the removal of what its rename left behind, and its own
            # ledger entry land together or not at all. A concept a human owns was
            # never staged, so it is not placed and not removed
            staging.commit(
                area,
                ledger,
                unit=f"`{item.uri}`",
                place=[cid for cid in produced if cid not in outcome.protected],
                remove=outcome.removed,
            )
            log_entries.extend(
                f"**Deprecation**: Removed `{cid}`, no longer produced by `{item.uri}`."
                for cid in outcome.removed
            )
            log_entries.extend(lines)
            _report(
                progress,
                Progress(SOURCE, item.uri, detail=f"{outcome.concept_count} concept(s)"),
            )

        if not dry_run:
            assert area is not None
            # after every concept for the run exists: a link can only point at
            # a concept that has been written
            _weave_run(config, area, ledger, summary, progress)
            if selected is None:
                # a partial run never looked at the other sources, so their
                # absence from `discovered` is not evidence that they are gone
                _deprecate_vanished(config, area, ledger, discovered, summary, log_entries)
    finally:
        # Concepts are published per source, but only the indexes, the log and the
        # okflint manifest make them a bundle. Run on the way out however we leave,
        # so an interrupted run's committed sources are indexed and logged rather
        # than sitting there unlisted.
        if not dry_run and config.bundle_root.exists():
            _report(progress, Progress(FINALIZE))
            _finalize(config, log_entries)

    return summary


def _report(progress: Callable[[Progress], None] | None, event: Progress) -> None:
    if progress is None:
        return
    try:
        progress(event)
    except Exception:  # a reporter must not be able to fail a run already paid for
        pass


def _ingest_single(
    *,
    config: Config,
    area: staging.Area,
    ledger: Ledger,
    item: SourceItem,
    text: str,
    source_id: str,
    outcome: ItemOutcome,
    taken: set[str],
    summary: IngestSummary,
    existing: list[tuple[str, str]],
    progress: Callable[[Progress], None] | None = None,
) -> list[str]:
    """Short enough to be one concept (§4.3). Returns its log lines."""
    _report(progress, Progress(SYNTHESIZE, item.uri, index=1, total=1))
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
    verb = _write(
        config, area, ledger, _build(config, item, result, concept_id, source_id), outcome
    )
    outcome.concept_id = concept_id
    return _log_lines(verb, result.title, concept_id, item.uri)


def _ingest_segmented(
    *,
    config: Config,
    area: staging.Area,
    ledger: Ledger,
    item: SourceItem,
    text: str,
    source_id: str,
    outcome: ItemOutcome,
    taken: set[str],
    summary: IngestSummary,
    existing: list[tuple[str, str]],
    progress: Callable[[Progress], None] | None = None,
) -> list[str]:
    """Plan first, then one call per section, then assemble the parent.

    A failed section is recorded and skipped: one refusal or one 429 must not
    discard the sections that did synthesize.
    """
    _report(progress, Progress(PLAN, item.uri))
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
        _report(
            progress,
            Progress(SYNTHESIZE, item.uri, index=index, total=total, detail=section.title),
        )
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
        verb = _write(
            config, area, ledger, _build(config, item, result, child_id, source_id), outcome
        )
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
        area,
        ledger,
        _build_parent(config, item, outline, parent_id, source_id, children),
        outcome,
    )
    outcome.concept_id = parent_id
    log_lines[:0] = _log_lines(verb, outline.title, parent_id, item.uri)
    return log_lines


def _write(
    config: Config,
    area: staging.Area,
    ledger: Ledger,
    concept: Concept,
    outcome: ItemOutcome,
) -> str:
    """Stage a concept unless a human owns it (§6.4). Returns the log verb.

    Read from the published bundle, written into the staging area: what protects a
    concept and whether one existed are facts about the tree a reader can see, and
    the source's commit is what makes this write one of them.

    A protected concept's replacement goes to `.okragged/proposed/` for the human
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
    okf.write_concept(area.bundle, concept)
    ledger.note_concept(concept_id, outcome.uri, concept.body)
    return "Update" if existed else "Creation"


def _log_lines(verb: str, title: str, concept_id: str, uri: str) -> list[str]:
    if not verb:
        return []  # nothing changed on disk; the human's copy still stands
    word = "Added" if verb == "Creation" else "Rewrote"
    return [f"**{verb}**: {word} [{title}](/{concept_id}.md) from `{uri}`."]


def _selected(config: Config, only: list[str] | None) -> set[str] | None:
    """The source uris a partial ingest is limited to. None means every source."""
    if only is None:
        return None
    root = config.project_root.resolve()
    uris: set[str] = set()
    for raw in only:
        path = Path(raw)
        path = path if path.is_absolute() else config.project_root / path
        try:
            uris.add(path.resolve().relative_to(root).as_posix())
        except ValueError:
            raise ValueError(f"{raw!r} is outside the project") from None
    return uris


def _index(bundle_root: Path) -> list[tuple[str, str]]:
    """The bundle's concepts as `(id, description)`, for a prompt prefix."""
    return [(ref.concept_id, ref.description) for ref in okf.inventory(bundle_root)]


def _weave_run(
    config: Config,
    area: staging.Area,
    ledger: Ledger,
    summary: IngestSummary,
    progress: Callable[[Progress], None] | None = None,
) -> None:
    """Rewrite this run's concepts with cross-links woven into their prose (§6.5).

    Only what this run wrote: a concept that was skipped is unchanged output and
    rewriting it would cost a model call to produce a file nobody asked for. The
    consequence is that links point forward from new concepts to old ones and not
    the other way round until the old one is itself re-ingested.

    This runs after every source has committed, so it rewrites concepts that are
    already published — and each rewrite is a commit of its own, because the body
    and the hash the ledger notes for it have to agree. An interrupt here leaves
    every concept either woven and re-recorded or unwoven and recorded; a concept
    left unwoven is the state a *failed* weave already produces (§6.5), so its
    source stays `complete` and is not retried for it.
    """
    # titles as well as descriptions: the pass has to choose link labels, and
    # prune_links needs a title when the model puts a raw path in the label
    index = [
        (ref.concept_id, ref.title, ref.description) for ref in okf.inventory(config.bundle_root)
    ]
    if len(index) < 2:
        return  # nothing to link to

    targets = [
        (outcome, concept_id)
        for outcome in summary.synthesized
        for concept_id in [outcome.concept_id, *outcome.child_ids]
        if concept_id not in outcome.protected
    ]
    for position, (outcome, concept_id) in enumerate(targets, start=1):
        _report(progress, Progress(WEAVE, concept_id, index=position, total=len(targets)))
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
        staging.discard(area)
        okf.write_concept(area.bundle, concept)
        # the post-weave hash, or the next run reads every concept as edited — and
        # committed with the body it describes, or an interrupt would leave exactly
        # that mismatch on disk for the next run to believe
        ledger.note_concept(concept_id, outcome.uri, concept.body)
        staging.commit(area, ledger, unit=f"the weave of `{concept_id}`", place=[concept_id])
        outcome.linked.append(concept_id)


def _stale(config: Config, ledger: Ledger, stale: set[str]) -> list[str]:
    """Concepts the previous run produced for this source and this one did not.

    Ids come from model-written titles, so a rebuild renames the tree as a matter
    of course; without this nothing ever removes what the rename left behind. The
    files go in the source's commit rather than here, so the moment when both
    namings are on disk is inside the window the pending record fences.
    """
    removed: list[str] = []
    for concept_id in sorted(stale):
        if ledger.protection(config.bundle_root, concept_id) is not None:
            continue  # a human owns it now; orphaned beats deleted
        if not (config.bundle_root / f"{concept_id}.md").exists():
            continue
        removed.append(concept_id)
        ledger.forget_concept(concept_id)
    return removed


def _deprecate_vanished(
    config: Config,
    area: staging.Area,
    ledger: Ledger,
    discovered: set[str],
    summary: IngestSummary,
    log_entries: list[str],
) -> None:
    """A source that is gone deprecates its concepts; it does not delete them.

    The spec keeps deprecated concepts for links and history (§6.1) — deletion
    is an explicit `okragged prune`, never a side effect of removing an input file.

    One commit per vanished source, for the same reason the boundary is the source
    everywhere else: the status change and the ledger forgetting it are one fact.
    """
    for uri in sorted(set(ledger.sources) - discovered):
        staging.discard(area)
        staged: list[str] = []
        for concept_id in ledger.forget_source(uri):
            if okf.set_status(config.bundle_root, concept_id, "deprecated", out_root=area.bundle):
                staged.append(concept_id)
        staging.commit(area, ledger, unit=f"the deprecation of `{uri}`", place=staged)
        for concept_id in staged:
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


def _finalize(config: Config, log_entries: list[str]) -> None:
    """The three artifacts that make published concepts a bundle rather than files.

    None of them is fenced by a commit, and none needs to be, because each is
    derivable from the bundle on disk: `write_indexes` reads the whole tree rather
    than accumulating during the run (§6.6), and `okf-base.yaml` is generated from
    the config. A run that dies before this point leaves indexes that do not
    mention its newest concepts, and *any* later run regenerates them — including
    one that changes nothing. `log.md` is the exception in kind but not in
    consequence: it is a narration nothing reads back, and it is appended here in
    a `finally`, so an interrupt still records the sources that did commit.

    **The ledger is deliberately not here.** It lands with the source it describes
    (§6.6) and is the one thing that must never be written outside a commit: a
    ledger saved on the way out could describe a concept whose file never landed,
    and the next run's content-hash skip would believe work was done that was not.
    """
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
        extra={"x_okragged_source_hash": item.content_sha256},
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
