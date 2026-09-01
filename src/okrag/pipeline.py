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
    """One event from a run in flight, for a caller that can't wait for the summary.

    Frozen and flat on purpose: an event that might get serialized to a browser
    (e.g. an HTTP progress endpoint) is the wrong place for a live object graph.
    `unit` plus `index`/`total` identify what's happening; `detail` is a short
    note for a human, not something a caller parses.
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
    """concept_id -> why a human owns it; the replacement went to .okrag/proposed/."""
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
    """Concepts whose source is gone. Marked, not deleted (DESIGN.md §6.1)."""
    invented_links: list[str] = field(default_factory=list)
    """Weave targets outside the bundle. Unwrapped to plain text, never silent."""
    recovered: list[str] = field(default_factory=list)
    """What this run found left behind by a previous one, and what it did about
    it (DESIGN.md §6.6: the staged commit). Reported rather than fixed silently,
    since it means an earlier build died."""

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
    """Runs the pipeline over `config`'s sources.

    `contract` regenerates `agent/AGENTS.md` (the docs an AI agent reads to use
    this bundle, DESIGN.md §10) after the emit, and defaults to on — a run that
    commits is exactly the moment the bundle changed, and the contract must
    never describe one that no longer exists. `contract=False` skips it for a
    caller who wants only the bundle.

    **Never registers the MCP server.** `.mcp.json` is the machine's own MCP
    client config, not derived from the bundle, so nothing here touches it —
    that's `okrag agent --register` or `agent.register()` (see
    `agent/contract.py`).

    `progress` is called as the run moves through its stages; see `Progress`.

    A real run holds the project lock and stages each source's emit, committing
    per source rather than per run (`staging.py`, DESIGN.md §6.6). So: a second
    writer is refused rather than interleaved; an interrupted run leaves every
    finished source published *and* recorded, so the next run only pays for the
    remainder; and the one source that was in flight stays unpublished in the
    sidecar until the next run reports and clears it.
    """
    if dry_run:
        # no lock, no staging: a dry run writes no bundle and no ledger, so
        # there's nothing to serialize against another writer
        swept = _run(config, dry_run=True, only=only, progress=progress)
        return _validated(config, swept, progress)

    with lock.hold(config.sidecar_root, "ingest") as held:
        # finish a half-applied commit from an earlier run before anything else,
        # even if this run then can't proceed for its own reasons
        notes = [note for note in (held.note, staging.reconcile(config)) if note]
        require_credential(config)  # before extracting a whole corpus, not after
        area = staging.prepare(config)
        summary = _run(config, dry_run=False, only=only, area=area, progress=progress)
        summary.recovered = notes
        staging.discard(area)  # nothing staged here was left uncommitted
        if contract and config.bundle_root.exists():
            # after the commits, never before: the contract describes the bundle,
            # so writing it beside concepts that didn't land would describe ones
            # that don't exist. A run that dies leaves the previous contract in
            # place, under-describing the bundle until the next run fixes it —
            # same self-healing direction as the indexes. AGENTS.md only: this
            # never touches the machine's MCP config
            write_contract(config)
    # outside the lock: validation just reads the published bundle and spawns
    # okflint, so holding the lock here buys nothing — a concurrent build only
    # means the report describes a newer tree, which is the one a reader will find
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
    """Runs the pipeline stages themselves.

    Every *read* is of the published bundle (`config.bundle_root`); every
    *write* goes into `area`, published one commit per source (DESIGN.md §6.6).
    Those two roots are all that "staged" means here — there's no separate copy
    of the bundle to keep in sync.
    """
    selected = _selected(config, only)
    source = LocalFileSource(
        root=config.sources_root,
        include=config.local.include,
        exclude=config.local.exclude,
        project_root=config.project_root,
    )
    summary = IngestSummary()
    # saved only by a commit: a ledger record is true only once its files have landed
    ledger = Ledger() if dry_run else Ledger.load(config.sidecar_root / LEDGER_NAME)
    ledger.bundle_root = config.bundle.root
    # snapshotted once: the synthesis prefix has to stay byte-identical all run
    existing = [] if dry_run else _index(config.bundle_root)
    # every id from the last run stays reserved until its own source releases it,
    # so a skipped source can't have its id stolen
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
                # a failed section is missing bundle content, so the next run
                # redoes this file rather than skipping it until the bytes change
                complete=not outcome.section_errors,
            )
            outcome.removed = _stale(config, ledger, previous - set(produced))
            # the source is the commit boundary (DESIGN.md §6.6): its new
            # concepts, the stale output its rename left behind, and its ledger
            # entry land together or not at all. A human-owned concept was never
            # staged, so it's neither placed nor removed
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
        # concepts publish per source, but only the indexes, the log, and the
        # okflint manifest make them a bundle — always regenerated on the way
        # out, so an interrupted run's committed sources are still indexed and
        # logged rather than sitting there unlisted
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
    """Short enough to become one concept, not several (DESIGN.md §4.3). Returns
    its log lines."""
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
    """Stages a concept unless a human owns it already (DESIGN.md §6.4). Returns
    the log verb.

    Reads from the published bundle, writes into the staging area — whether a
    concept is protected, and whether one already existed, are both facts about
    the tree a reader can currently see.

    A protected concept's replacement goes to `.okrag/proposed/` for the
    human to resolve instead — the one place a human's edit overrides the model's.
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
    """Rewrites this run's concepts with cross-links woven into their prose
    (DESIGN.md §6.5: link-weave).

    Only concepts this run actually wrote — a skipped concept is unchanged
    output, and rewriting it would spend a model call on a file nobody asked
    for. So links point forward, from new concepts to old ones, until an old
    one is itself re-ingested.

    Runs after every source has committed, rewriting already-published
    concepts — each rewrite is its own commit, since the ledger's hash for a
    concept has to match its body. An interrupt here leaves every concept
    either woven-and-recorded or unwoven-and-recorded (the same state a
    *failed* weave already produces), so its source stays `complete` and
    isn't retried just for that.
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
        # records the post-weave hash — otherwise the next run reads every
        # woven concept as human-edited
        ledger.note_concept(concept_id, outcome.uri, concept.body)
        staging.commit(area, ledger, unit=f"the weave of `{concept_id}`", place=[concept_id])
        outcome.linked.append(concept_id)


def _stale(config: Config, ledger: Ledger, stale: set[str]) -> list[str]:
    """Concepts the previous run produced for this source, that this run didn't.

    Concept ids come from model-written titles, so a rebuild renames the tree
    as a matter of course — without this, nothing would ever remove what the
    rename left behind. Actually deleting the files happens in the source's
    commit, not here, so the window where both namings exist on disk stays
    inside what the pending-commit record fences.
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
    """A vanished source deprecates its concepts; it never deletes them.

    Deprecated concepts stay for links and history (DESIGN.md §6.1) — deletion
    is an explicit `okrag prune`, never a side effect of an input file
    disappearing.

    One commit per vanished source, same as everywhere else: the status change
    and the ledger forgetting it are one fact, landing together or not at all.
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
    """Writes the three artifacts that make published concepts a bundle, not
    just files on disk: the indexes, the okflint manifest, and `log.md`.

    None of these needs a commit fence, because each is derived fresh from the
    bundle on disk rather than accumulated during the run — a run that dies
    early just leaves indexes that don't mention its newest concepts, and any
    later run regenerates them. `log.md` is the odd one out (it's appended to,
    not regenerated), but the reasoning still holds: it's a narration nothing
    reads back, so recording only what actually committed is enough.

    **The ledger is deliberately not written here.** It lands with the source
    it describes (DESIGN.md §6.6) and must never be written outside a commit —
    saving it on the way out could describe a concept whose file never landed,
    fooling the next run's content-hash skip into thinking the work was done.
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
        extra={"x_okrag_source_hash": item.content_sha256},
    )


def _build_parent(
    config: Config,
    item: SourceItem,
    outline: Outline,
    parent_id: str,
    source_id: str,
    children: list[tuple[str, str, str]],
) -> Concept:
    """The parent's body is the outline's overview, plus a list linking down to
    each child.

    The reverse direction — each child linking back to its parent in prose
    (DESIGN.md §4.3) — belongs to link-weave instead (`_weave_run` above,
    which is told the parent). Writing that sentence here would be inventing
    content the model never wrote.
    """
    lines = [outline.overview.strip(), "", "# Sections", ""]
    for slug, title, description in children:
        suffix = f" - {description}" if description else ""
        lines.append(f"- [{title}](/{parent_id}/{slug}.md){suffix}")
    # no footnote: `sources` already carries provenance, and footnotes are for
    # per-claim attribution (DESIGN.md §5.1) — this overview-plus-links makes no
    # specific claim needing one
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
