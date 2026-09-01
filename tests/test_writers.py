"""§13 M5 item 6: one writer per project, and an emit that commits per source.

After M5 the CLI, the MCP server's `add_source` and the Python API are three
writers to one directory. Concept ids come from model-written titles, so two of
them interleaving do not overwrite each other's names — they write two different
trees into one directory, and half of one source's emit is a mix of two namings
rather than a subset of the old one.

**The commit boundary is the source.** That hazard lives inside one source's emit,
between writing its new concepts and removing the stale output its rename left
behind, and that window is what the pending record fences. Across sources there is
nothing to fence: source A's new concepts beside source B's untouched old ones is a
complete, valid bundle — so a run interrupted at source thirty-nine of forty keeps
what it paid for.

These tests drive the failures rather than the happy path: a live holder refusing a
second writer, a dead one reclaimed with no human in the loop, a run interrupted
between sources, a commit interrupted half way through placing one, and the two
interrupts around the weave.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from okrag import agent, lock, okf, pipeline, staging
from okrag import config as config_module
from okrag.ledger import LEDGER_NAME, Ledger, body_hash
from okrag.synth import WeaveResult

# tests/ has no __init__.py, so pytest imports these as top-level modules
from test_pipeline import (  # noqa: F401  (project is used as a fixture)
    SAMPLE,
    fake_synthesize,
    fake_weave,
    project,
)


def now(offset_seconds: float = 0) -> str:
    at = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return at.isoformat(timespec="seconds").replace("+00:00", "Z")


def write_lock(sidecar: Path, **fields) -> Path:
    """A lock file as some other run would have left it."""
    path = sidecar / lock.LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "version": lock.VERSION,
        "operation": "build",
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started": now(),
        **fields,
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def a_pid_that_is_gone() -> int:
    """A real pid belonging to a process that has certainly exited."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    proc.wait()
    return proc.pid


def no_model(**_):
    raise AssertionError("this run must not reach the model")


def interrupt(**_):
    raise KeyboardInterrupt


# --- the lock -------------------------------------------------------------------


def test_a_second_writer_is_refused_while_a_live_lock_is_held(tmp_path):
    sidecar = tmp_path / ".okrag"
    with lock.hold(sidecar, "build") as held:
        with pytest.raises(lock.LockHeld) as refused:
            with lock.hold(sidecar, "add_source"):
                raise AssertionError("two writers held the project at once")

    message = str(refused.value)
    assert "build" in message and str(held.holder.pid) in message
    assert socket.gethostname() in message
    assert not (sidecar / lock.LOCK_NAME).exists()  # released on the way out


def test_a_build_is_refused_while_another_writer_holds_the_project(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", no_model)
    cfg = config_module.load(project)

    with lock.hold(cfg.sidecar_root, "add_source"):
        with pytest.raises(lock.LockHeld):
            pipeline.ingest(cfg, dry_run=False)

    assert not cfg.bundle_root.exists()


def test_a_dry_run_is_not_refused_by_a_writer_that_holds_the_project(project, monkeypatch):
    """It writes no bundle and no ledger, so there is nothing to serialize — and a
    read-only command refused because a build is running would be a new failure."""
    monkeypatch.setattr(pipeline, "synthesize", no_model)
    cfg = config_module.load(project)

    with lock.hold(cfg.sidecar_root, "build"):
        summary = pipeline.ingest(cfg, dry_run=True)

    assert [o.uri for o in summary.written] == ["sources/revenue-policy.md"]


def test_a_lock_left_by_a_dead_run_is_reclaimed_without_a_human(project, monkeypatch):
    """A crashed run must not need a file deleted by hand before the next build."""
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    path = write_lock(cfg.sidecar_root, pid=a_pid_that_is_gone())

    summary = pipeline.ingest(cfg, dry_run=False)

    assert any("reclaimed" in note for note in summary.recovered), summary.recovered
    assert (cfg.bundle_root / "revenue-policy.md").exists()
    assert not path.exists()


def test_an_unreadable_lock_file_does_not_wedge_the_tool(tmp_path):
    sidecar = tmp_path / ".okrag"
    sidecar.mkdir()
    (sidecar / lock.LOCK_NAME).write_text("{ not json", encoding="utf-8", newline="\n")

    with lock.hold(sidecar, "build") as held:
        assert "unreadable" in held.note
        recorded = json.loads((sidecar / lock.LOCK_NAME).read_text(encoding="utf-8-sig"))
    assert recorded["pid"] == os.getpid()


def test_a_lock_from_another_host_is_judged_by_age_and_not_by_pid(tmp_path):
    """The pid in it names a process on a machine this one cannot ask about."""
    sidecar = tmp_path / ".okrag"
    write_lock(sidecar, host="another-machine")
    with pytest.raises(lock.LockHeld):
        with lock.hold(sidecar, "build"):
            pass

    write_lock(
        sidecar,
        host="another-machine",
        started=now(-lock.FOREIGN_STALE_SECONDS - 60),
    )
    with lock.hold(sidecar, "build") as held:
        assert "reclaimed" in held.note


def test_the_lock_is_released_when_the_run_it_guards_raises(tmp_path):
    sidecar = tmp_path / ".okrag"
    with pytest.raises(RuntimeError):
        with lock.hold(sidecar, "build"):
            raise RuntimeError("the provider went away")
    assert not (sidecar / lock.LOCK_NAME).exists()


def test_a_lock_another_run_reclaimed_is_not_deleted_on_release(tmp_path):
    """Otherwise a run judged dead deletes the live run's lock on its way out, and
    two writers proceed with no lock at all — worse than the state this prevents."""
    sidecar = tmp_path / ".okrag"
    with lock.hold(sidecar, "build"):
        write_lock(sidecar, host="another-machine", operation="index", pid=os.getpid() + 1)

    recorded = json.loads((sidecar / lock.LOCK_NAME).read_text(encoding="utf-8-sig"))
    assert recorded["operation"] == "index"


def test_every_read_path_answers_while_a_write_holds_the_lock(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    with lock.hold(cfg.sidecar_root, "build"):
        assert agent.search(cfg, "revenue", k=3).hits
        assert "Revenue" in agent.read_concept(cfg, "revenue-policy")
        assert agent.lint(cfg).concepts == 1
        assert okf.validate(cfg.bundle_root).ok


# --- the staged emit, one source at a time --------------------------------------


def three_sources(project: Path) -> list[str]:
    """Two more sources beside the fixture's, in discovery order. Returns their ids."""
    for name in ("zz-one", "zz-two"):
        (project / "sources" / f"{name}.md").write_text(
            SAMPLE + f"\nAbout {name}.\n", encoding="utf-8"
        )
    return ["revenue-policy", "zz-one", "zz-two"]


def counting(fake, stop_before: str | None = None):
    """A synthesis stub that counts its calls, and optionally dies on one source."""
    calls: list[str] = []

    def synthesize(**kwargs):
        if stop_before is not None and kwargs["source_uri"].endswith(stop_before):
            raise KeyboardInterrupt
        calls.append(kwargs["source_uri"])
        return fake(**kwargs)

    synthesize.calls = calls  # type: ignore[attr-defined]
    return synthesize


def test_a_source_is_published_by_its_own_commit_and_not_by_the_run(project, monkeypatch):
    """Across sources there is no hazard to fence: the first source's new concepts
    beside the third's absence is a complete, self-consistent bundle."""
    ids = three_sources(project)
    cfg = config_module.load(project)
    staged = cfg.sidecar_root / staging.STAGING / staging.BUNDLE
    seen: list[tuple[str, bool, bool]] = []

    def watch(event):
        if event.stage == pipeline.SYNTHESIZE:
            seen.append(
                (
                    event.unit,
                    (cfg.bundle_root / f"{ids[0]}.md").exists(),
                    (staged / f"{ids[0]}.md").exists(),
                )
            )

    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    pipeline.ingest(cfg, dry_run=False, progress=watch)

    first, second, third = seen
    # while the first source is being synthesized it is neither staged nor published
    assert first == ("sources/revenue-policy.md", False, False)
    # by the time the others run it is published, and nothing of it is left staged
    assert second == ("sources/zz-one.md", True, False)
    assert third == ("sources/zz-two.md", True, False)
    assert all((cfg.bundle_root / f"{cid}.md").exists() for cid in ids)


def test_a_finished_build_leaves_no_staging_area_behind(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    for leftover in (
        cfg.sidecar_root / staging.STAGING,
        cfg.sidecar_root / staging.PENDING_NAME,
    ):
        assert not leftover.exists(), leftover


def test_the_ledger_never_describes_a_source_whose_concepts_are_not_published(
    project, monkeypatch
):
    """Per source now rather than per run, and this is the direction that matters.

    A ledger that outran the bundle would have the next run's content-hash skip
    believe work was done that was not, and the corpus would lose that source
    silently. The other direction only costs a re-synthesis.
    """
    ids = three_sources(project)
    monkeypatch.setattr(pipeline, "synthesize", counting(fake_synthesize, stop_before="zz-two.md"))
    cfg = config_module.load(project)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    ledger = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    assert sorted(ledger.sources) == ["sources/revenue-policy.md", "sources/zz-one.md"]
    for concept_id in ledger.concepts:
        assert (cfg.bundle_root / f"{concept_id}.md").exists(), concept_id
    assert not (cfg.bundle_root / f"{ids[2]}.md").exists()


def test_a_second_run_pays_only_for_the_sources_the_interrupted_one_never_committed(
    project, monkeypatch
):
    """The point of moving the boundary. A forty-source build interrupted at
    thirty-nine used to re-pay thirty-nine sources' worth of model calls."""
    ids = three_sources(project)
    first = counting(fake_synthesize, stop_before="zz-two.md")
    monkeypatch.setattr(pipeline, "synthesize", first)
    cfg = config_module.load(project)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)
    assert first.calls == ["sources/revenue-policy.md", "sources/zz-one.md"]

    second = counting(fake_synthesize)
    monkeypatch.setattr(pipeline, "synthesize", second)
    woven: list[str] = []
    monkeypatch.setattr(
        pipeline, "weave", lambda **kw: woven.append(kw["concept_id"]) or fake_weave(**kw)
    )
    summary = pipeline.ingest(cfg, dry_run=False)

    assert second.calls == ["sources/zz-two.md"], "the committed sources must cost nothing"
    assert woven == [ids[2]], "and only the source this run wrote is woven"
    assert [o.uri for o in summary.synthesized] == ["sources/zz-two.md"]
    assert len(summary.skipped) == 2
    assert all((cfg.bundle_root / f"{cid}.md").exists() for cid in ids)


def test_the_one_source_in_flight_when_a_run_died_waits_in_the_sidecar(project, monkeypatch):
    """Nothing is deleted at the moment of failure — but only that source's files
    are there, because every source before it already committed and cleared."""
    three_sources(project)
    cfg = config_module.load(project)

    def die_after_writing_the_third(**kwargs):
        result = fake_synthesize(**kwargs)
        if kwargs["source_uri"].endswith("zz-two.md"):
            # the concept is staged, and then the run dies before its commit
            okf.write_concept(
                staging.area_for(cfg).bundle,
                okf.Concept(
                    concept_id="zz-two",
                    type="Policy",
                    title="Zz Two",
                    description="staged, never committed",
                    body="Body.",
                ),
            )
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(pipeline, "synthesize", die_after_writing_the_third)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    staged = cfg.sidecar_root / staging.STAGING / staging.BUNDLE
    assert (staged / "zz-two.md").exists()
    assert not (staged / "revenue-policy.md").exists(), "a committed source clears itself"

    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    summary = pipeline.ingest(cfg, dry_run=False)

    assert any("discarded 1 concept file" in note for note in summary.recovered), summary.recovered
    assert not (cfg.sidecar_root / staging.STAGING).exists()


def test_a_no_op_build_reports_nothing_recovered(project, monkeypatch):
    """A run that stages nothing must not leave an empty area for the next one to
    report as debris — the report means an earlier run died."""
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    monkeypatch.setattr(pipeline, "synthesize", no_model)
    assert pipeline.ingest(cfg, dry_run=False).recovered == []


def test_a_commit_interrupted_while_placing_a_concept_is_finished_by_the_next_run(
    project, monkeypatch
):
    """The record is written before the first file moves, and it is what makes a
    half-applied commit something the next run finishes rather than reconciles."""
    three_sources(project)
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)

    real_replace = os.replace
    fail = {"on": True}

    def die_placing_the_last_concept(src, dst):
        if fail["on"] and Path(dst) == cfg.bundle_root / "zz-two.md":
            raise OSError("interrupted while placing a concept")
        return real_replace(src, dst)

    monkeypatch.setattr(staging.os, "replace", die_placing_the_last_concept)
    with pytest.raises(OSError):
        pipeline.ingest(cfg, dry_run=False)

    assert not (cfg.bundle_root / "zz-two.md").exists()
    assert (cfg.bundle_root / "zz-one.md").exists(), "the sources before it committed"
    assert (cfg.sidecar_root / staging.PENDING_NAME).exists()

    fail["on"] = False
    monkeypatch.setattr(pipeline, "synthesize", no_model)  # every source unchanged
    summary = pipeline.ingest(cfg, dry_run=False)

    assert any("completed the commit" in note for note in summary.recovered), summary.recovered
    assert (cfg.bundle_root / "zz-two.md").exists()
    assert not (cfg.sidecar_root / staging.PENDING_NAME).exists()
    # the indexes are derived, so a run that finds them stale regenerates them
    assert "zz-two.md" in (cfg.bundle_root / "index.md").read_text(encoding="utf-8-sig")
    assert sorted(Ledger.load(cfg.sidecar_root / LEDGER_NAME).sources) == [
        "sources/revenue-policy.md",
        "sources/zz-one.md",
        "sources/zz-two.md",
    ]


def test_a_commit_interrupted_between_placing_and_removing_is_finished_by_the_next_run(
    project, monkeypatch
):
    """The window the record exists to fence, driven directly.

    A re-ingest under a new naming places the new files and removes what the rename
    left behind, and *between* those two the source owns both namings. Ordering
    inside the fence is place-then-remove on purpose: within the window both on
    disk is a superset of the truth, and neither of them is not.

    Driven through `staging.commit` rather than through a rebuild because ids for
    an unsegmented source come from its filename — the model-written titles that
    make a tree rename wholesale are the segmented path's.
    """
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    assert (cfg.bundle_root / "revenue-policy.md").exists()

    area = staging.prepare(cfg)
    okf.write_concept(
        area.bundle,
        okf.Concept(
            concept_id="renamed-policy",
            type="Policy",
            title="Renamed Policy",
            description="the same source under the naming this run chose",
            body="Body.",
        ),
    )
    ledger = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    ledger.note_concept("renamed-policy", "sources/revenue-policy.md", "Body.")
    ledger.forget_concept("revenue-policy")
    # the source now owns the new id, exactly as `_run` records before it commits
    ledger.sources["sources/revenue-policy.md"].concepts = ["renamed-policy"]

    real_delete = staging.delete_concept
    fail = {"on": True}

    def die_removing_the_stale_concept(*args, **kwargs):
        if fail["on"]:
            raise OSError("interrupted after placing, before removing")
        return real_delete(*args, **kwargs)

    monkeypatch.setattr(staging, "delete_concept", die_removing_the_stale_concept)
    with pytest.raises(OSError):
        staging.commit(
            area,
            ledger,
            unit="`sources/revenue-policy.md`",
            place=["renamed-policy"],
            remove=["revenue-policy"],
        )

    # both namings on disk: a superset of the truth, and the record says which wins
    assert (cfg.bundle_root / "renamed-policy.md").exists()
    assert (cfg.bundle_root / "revenue-policy.md").exists()
    assert (cfg.sidecar_root / staging.PENDING_NAME).exists()
    assert "revenue-policy" in Ledger.load(cfg.sidecar_root / LEDGER_NAME).concepts, (
        "the ledger is published by the commit, so it has not moved yet"
    )

    fail["on"] = False
    monkeypatch.setattr(pipeline, "synthesize", no_model)  # the source is unchanged
    summary = pipeline.ingest(cfg, dry_run=False)

    assert any("completed the commit" in note for note in summary.recovered), summary.recovered
    assert (cfg.bundle_root / "renamed-policy.md").exists()
    assert not (cfg.bundle_root / "revenue-policy.md").exists(), "the stale naming goes"
    published = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    assert "renamed-policy" in published.concepts
    assert "revenue-policy" not in published.concepts
    assert not (cfg.sidecar_root / staging.PENDING_NAME).exists()


def test_a_vanished_source_deprecates_and_is_forgotten_in_one_commit(project, monkeypatch):
    """One commit per vanished source, for the reason the boundary is the source
    everywhere else: the status change and the ledger forgetting it are one fact."""
    three_sources(project)
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    (project / "sources" / "zz-one.md").unlink()
    monkeypatch.setattr(pipeline, "synthesize", no_model)  # the survivors are unchanged
    summary = pipeline.ingest(cfg, dry_run=False)

    assert summary.deprecated == ["zz-one"]
    concept = okf.parse(
        (cfg.bundle_root / "zz-one.md").read_text(encoding="utf-8-sig"), "zz-one"
    )
    assert concept.status == "deprecated", "kept for links and history (§6.1), not deleted"

    ledger = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    assert "sources/zz-one.md" not in ledger.sources
    assert "zz-one" not in ledger.concepts
    assert not (cfg.sidecar_root / staging.PENDING_NAME).exists()
    assert not (cfg.sidecar_root / staging.STAGING).exists()


def test_a_record_that_cannot_be_read_still_publishes_what_was_staged(project, monkeypatch):
    """Last resort: the record is the plan, and losing it must not lose the work.

    Its *presence* is the fact that matters — a corrupt one read as debris would
    roll back a commit that is half applied — so it degrades to placing every file
    the staging area holds.
    """
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    area = staging.area_for(cfg)
    okf.write_concept(
        area.bundle,
        okf.Concept(
            concept_id="zz-staged",
            type="Policy",
            title="Zz Staged",
            description="staged behind a record nothing can read",
            body="Body.",
        ),
    )
    (cfg.sidecar_root / staging.PENDING_NAME).write_text(
        "{ not json", encoding="utf-8", newline="\n"
    )

    note = staging.reconcile(cfg)

    assert "unreadable" in note
    assert (cfg.bundle_root / "zz-staged.md").exists()
    assert not (cfg.sidecar_root / staging.PENDING_NAME).exists()
    assert not (cfg.sidecar_root / staging.STAGING).exists()


# --- the weave, which rewrites concepts already published ------------------------


def hashes_agree(cfg) -> list[str]:
    """Concept ids whose body on disk does not hash to what the ledger recorded.

    Empty is the invariant: a mismatch is read as a human edit (§6.4), so a
    concept woven against a stale hash is one no later run will ever touch again.
    """
    ledger = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    mismatched = []
    for concept_id, record in ledger.concepts.items():
        path = cfg.bundle_root / f"{concept_id}.md"
        concept = okf.parse(path.read_text(encoding="utf-8-sig"), concept_id)
        if body_hash(concept.body) != record.body_sha256:
            mismatched.append(concept_id)
    return mismatched


def test_an_interrupt_before_the_weave_publishes_concepts_with_no_cross_links(
    project, monkeypatch
):
    """The state a *failed* weave already produces (§6.5), so it is not a new one.

    A weave failure leaves a valid, link-less concept and deliberately does not
    mark the source incomplete — linking is an upgrade to a concept, not a
    precondition for one — and an interrupt before the pass must land there too.
    """
    ids = three_sources(project)
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline, "weave", interrupt)
    cfg = config_module.load(project)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    ledger = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    assert sorted(ledger.sources) == [
        "sources/revenue-policy.md",
        "sources/zz-one.md",
        "sources/zz-two.md",
    ]
    assert all(record.complete for record in ledger.sources.values()), "not a failed section"
    assert hashes_agree(cfg) == []
    for concept_id in ids:
        body = (cfg.bundle_root / f"{concept_id}.md").read_text(encoding="utf-8-sig")
        assert "](/zz-" not in body, "nothing was woven, so nothing links"

    # and the next run skips them rather than retrying for the links it lacks
    monkeypatch.setattr(pipeline, "synthesize", no_model)
    monkeypatch.setattr(pipeline, "weave", no_model)
    assert len(pipeline.ingest(cfg, dry_run=False).skipped) == 3


def test_an_interrupt_during_the_weave_leaves_no_concept_woven_against_a_stale_hash(
    project, monkeypatch
):
    """Each rewrite is its own commit, so every concept is either woven and
    re-recorded or unwoven and recorded — never one against the other's hash."""
    ids = three_sources(project)

    def weave_the_first_then_die(**kwargs):
        if kwargs["concept_id"] != ids[0]:
            raise KeyboardInterrupt
        return WeaveResult(
            body_markdown=kwargs["body"] + f"\n\nSee [Zz One](/{ids[1]}.md).\n", invented=[]
        )

    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline, "weave", weave_the_first_then_die)
    cfg = config_module.load(project)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    assert hashes_agree(cfg) == []
    woven = (cfg.bundle_root / f"{ids[0]}.md").read_text(encoding="utf-8-sig")
    unwoven = (cfg.bundle_root / f"{ids[1]}.md").read_text(encoding="utf-8-sig")
    assert f"](/{ids[1]}.md)" in woven
    assert f"](/{ids[1]}.md)" not in unwoven

    # the hashes agreeing is what stops the next run reading either as hand-edited
    ledger = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    assert [cid for cid in ids if ledger.protection(cfg.bundle_root, cid)] == []
