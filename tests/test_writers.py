"""§13 M5 item 6: one writer per project, and an emit that commits or does not.

After M5 the CLI, the MCP server's `add_source` and the Python API are three
writers to one directory. Concept ids come from model-written titles, so two of
them interleaving do not overwrite each other's names — they write two different
trees into one directory, and a half-applied emit is a mix of two namings rather
than a subset of the old one.

These tests drive the failures rather than the happy path: a live holder refusing
a second writer, a dead one reclaimed with no human in the loop, a run
interrupted before its commit, and a commit interrupted half way through its swap.
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

from okragged import agent, lock, okf, pipeline, staging
from okragged import config as config_module
from okragged.ledger import LEDGER_NAME, Ledger

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
    sidecar = tmp_path / ".okragged"
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
    sidecar = tmp_path / ".okragged"
    sidecar.mkdir()
    (sidecar / lock.LOCK_NAME).write_text("{ not json", encoding="utf-8", newline="\n")

    with lock.hold(sidecar, "build") as held:
        assert "unreadable" in held.note
        recorded = json.loads((sidecar / lock.LOCK_NAME).read_text(encoding="utf-8-sig"))
    assert recorded["pid"] == os.getpid()


def test_a_lock_from_another_host_is_judged_by_age_and_not_by_pid(tmp_path):
    """The pid in it names a process on a machine this one cannot ask about."""
    sidecar = tmp_path / ".okragged"
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
    sidecar = tmp_path / ".okragged"
    with pytest.raises(RuntimeError):
        with lock.hold(sidecar, "build"):
            raise RuntimeError("the provider went away")
    assert not (sidecar / lock.LOCK_NAME).exists()


def test_a_lock_another_run_reclaimed_is_not_deleted_on_release(tmp_path):
    """Otherwise a run judged dead deletes the live run's lock on its way out, and
    two writers proceed with no lock at all — worse than the state this prevents."""
    sidecar = tmp_path / ".okragged"
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


# --- the staged emit ------------------------------------------------------------


def test_a_run_writes_nothing_into_the_bundle_until_it_commits(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    staged = cfg.sidecar_root / staging.STAGING / staging.BUNDLE
    seen: dict[str, bool] = {}

    def watch(event):
        if event.stage == pipeline.SOURCE:
            seen["published"] = (cfg.bundle_root / "revenue-policy.md").exists()
            seen["staged"] = (staged / "revenue-policy.md").exists()

    pipeline.ingest(cfg, dry_run=False, progress=watch)

    assert seen == {"published": False, "staged": True}
    assert (cfg.bundle_root / "revenue-policy.md").exists()


def test_a_finished_build_leaves_no_staging_area_behind(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    for leftover in (
        cfg.sidecar_root / staging.STAGING,
        cfg.sidecar_root / staging.SUPERSEDED,
        cfg.sidecar_root / staging.PENDING_NAME,
    ):
        assert not leftover.exists(), leftover


def test_an_interrupted_run_leaves_the_ledger_describing_the_published_bundle(
    project, monkeypatch
):
    """The ledger and the bundle commit together or neither does.

    A ledger that outran the bundle would have the next run's content-hash skip
    believe work was done that was not, and the corpus would lose that source
    silently — which is worse than losing the model calls the run had paid for.
    """
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    (project / "sources" / "zz-second.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(pipeline, "weave", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    ledger = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    assert list(ledger.sources) == ["sources/revenue-policy.md"]
    assert list(ledger.concepts) == ["revenue-policy"]
    assert not (cfg.bundle_root / "zz-second.md").exists()


def test_the_next_run_redoes_the_source_an_interrupted_one_never_published(
    project, monkeypatch
):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    (project / "sources" / "zz-second.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(pipeline, "weave", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    monkeypatch.setattr(pipeline, "weave", fake_weave)
    summary = pipeline.ingest(cfg, dry_run=False)

    assert [o.uri for o in summary.synthesized] == ["sources/zz-second.md"]
    assert (cfg.bundle_root / "zz-second.md").exists()


def test_the_work_an_interrupted_run_staged_waits_in_the_sidecar(project, monkeypatch):
    """Nothing is deleted at the moment of failure. The staged tree is complete —
    indexes and all — and the next run is what reports it and clears it."""
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    (project / "sources" / "zz-second.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(pipeline, "weave", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    staged = cfg.sidecar_root / staging.STAGING / staging.BUNDLE
    assert (staged / "zz-second.md").exists()

    monkeypatch.setattr(pipeline, "weave", fake_weave)
    summary = pipeline.ingest(cfg, dry_run=False)

    assert any("discarded" in note for note in summary.recovered), summary.recovered
    assert not (cfg.sidecar_root / staging.STAGING).exists()


def test_a_swap_interrupted_between_the_two_renames_is_finished_by_the_next_run(
    project, monkeypatch
):
    """The pending record is written before the first rename, and it is what makes
    a half-applied swap something the next run can finish rather than reconcile."""
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    (project / "sources" / "zz-second.md").write_text(SAMPLE, encoding="utf-8")

    real_rename = os.rename
    fail = {"on": True}

    def die_moving_the_new_tree_in(src, dst):
        if fail["on"] and Path(dst) == cfg.bundle_root:
            raise OSError("interrupted between the two renames")
        return real_rename(src, dst)

    monkeypatch.setattr(staging.os, "rename", die_moving_the_new_tree_in)
    with pytest.raises(OSError):
        pipeline.ingest(cfg, dry_run=False)

    # the window §6.6 admits to: for this moment the project has no bundle
    assert not cfg.bundle_root.exists()
    assert (cfg.sidecar_root / staging.PENDING_NAME).exists()

    fail["on"] = False
    monkeypatch.setattr(pipeline, "synthesize", no_model)  # both sources unchanged
    summary = pipeline.ingest(cfg, dry_run=False)

    assert any("completed" in note for note in summary.recovered), summary.recovered
    assert (cfg.bundle_root / "zz-second.md").exists()
    assert (cfg.bundle_root / "revenue-policy.md").exists()
    assert not (cfg.sidecar_root / staging.PENDING_NAME).exists()
    assert sorted(Ledger.load(cfg.sidecar_root / LEDGER_NAME).sources) == [
        "sources/revenue-policy.md",
        "sources/zz-second.md",
    ]


def test_a_bundle_parked_by_a_swap_whose_record_is_gone_is_put_back(project, monkeypatch):
    """Last resort: the record is the plan, and losing it must not lose the tree."""
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    area = staging.area_for(cfg)
    os.rename(cfg.bundle_root, area.superseded)

    note = staging.reconcile(cfg)

    assert "restored" in note
    assert (cfg.bundle_root / "revenue-policy.md").exists()
    assert not area.superseded.exists()
