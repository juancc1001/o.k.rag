"""M2 item 1: the ledger makes a second build over unchanged sources free.

The exit criterion in DESIGN §13 is exactly one assertion — re-run and observe
zero model calls — so every fake here raises if the model is reached at all.
"""

from __future__ import annotations

import json
import shutil

from ragged import config as config_module
from ragged import okf, pipeline
from ragged.ledger import LEDGER_NAME, Ledger, body_hash
from ragged.synth import ProviderHTTPError

# tests/ has no __init__.py, so pytest imports these as top-level modules
from test_pipeline import (  # noqa: F401  (project/segmented are used as fixtures)
    LONG,
    SAMPLE,
    fake_plan,
    fake_synthesize,
    project,
    segmented,
)


def no_model(**_):
    raise AssertionError("an unchanged source must not reach the model")


def ledger_of(cfg) -> Ledger:
    return Ledger.load(cfg.sidecar_root / LEDGER_NAME)


# --- the exit criterion ----------------------------------------------------------


def test_a_second_build_over_unchanged_sources_makes_no_model_call(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    monkeypatch.setattr(pipeline, "synthesize", no_model)
    monkeypatch.setattr(pipeline, "plan", no_model)
    second = pipeline.ingest(cfg, dry_run=False)

    assert [o.concept_id for o in second.skipped] == ["revenue-policy"]
    assert second.synthesized == []
    assert second.input_tokens == 0 and second.output_tokens == 0
    assert second.report is not None and second.report.ok


def test_a_segmented_source_is_skipped_whole_children_and_all(segmented, monkeypatch):
    cfg = config_module.load(segmented)
    pipeline.ingest(cfg, dry_run=False)

    monkeypatch.setattr(pipeline, "synthesize", no_model)
    monkeypatch.setattr(pipeline, "plan", no_model)
    second = pipeline.ingest(cfg, dry_run=False)

    tp = next(o for o in second.outcomes if o.uri == "sources/tp.md")
    assert tp.skipped
    assert tp.concept_id == "trabajo-practico"
    assert tp.child_ids == ["trabajo-practico/definicion", "trabajo-practico/doctrina"]


def test_a_skipped_source_keeps_its_place_in_the_root_index(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    monkeypatch.setattr(pipeline, "synthesize", no_model)
    pipeline.ingest(cfg, dry_run=False)

    index = (cfg.bundle_root / "index.md").read_text(encoding="utf-8-sig")
    assert "revenue-policy.md" in index
    assert "When revenue is recognized" in index


def test_a_no_op_build_adds_no_log_entries(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    before = (cfg.bundle_root / "log.md").read_text(encoding="utf-8-sig")

    monkeypatch.setattr(pipeline, "synthesize", no_model)
    pipeline.ingest(cfg, dry_run=False)

    assert (cfg.bundle_root / "log.md").read_text(encoding="utf-8-sig") == before


def test_a_no_op_build_leaves_the_ledger_byte_identical(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    path = cfg.sidecar_root / LEDGER_NAME
    before = path.read_bytes()

    monkeypatch.setattr(pipeline, "synthesize", no_model)
    pipeline.ingest(cfg, dry_run=False)

    assert path.read_bytes() == before


# --- what invalidates a skip -----------------------------------------------------


def test_an_edited_source_is_re_ingested(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    (project / "sources" / "revenue-policy.md").write_text(
        SAMPLE + "\nDiscounts are netted against revenue.\n", encoding="utf-8"
    )
    second = pipeline.ingest(cfg, dry_run=False)

    assert [o.concept_id for o in second.synthesized] == ["revenue-policy"]
    assert second.skipped == []


def test_a_deleted_bundle_rebuilds_even_though_the_sidecar_survived(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    shutil.rmtree(cfg.bundle_root)

    second = pipeline.ingest(cfg, dry_run=False)
    assert [o.concept_id for o in second.synthesized] == ["revenue-policy"]
    assert (cfg.bundle_root / "revenue-policy.md").exists()


def test_a_corrupt_ledger_costs_a_rebuild_not_a_failed_build(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    (cfg.sidecar_root / LEDGER_NAME).write_text("{ not json", encoding="utf-8", newline="\n")

    second = pipeline.ingest(cfg, dry_run=False)
    assert [o.concept_id for o in second.synthesized] == ["revenue-policy"]


def test_a_ledger_from_a_future_version_is_ignored(tmp_path):
    path = tmp_path / LEDGER_NAME
    path.write_text(json.dumps({"version": 99, "sources": {"x": {}}}), encoding="utf-8", newline="\n")
    assert Ledger.load(path).sources == {}


# --- vanished sources ------------------------------------------------------------


def test_a_vanished_source_deprecates_its_concepts_rather_than_deleting_them(
    project, monkeypatch
):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    (project / "sources" / "revenue-policy.md").unlink()
    (project / "sources" / "other.md").write_text(SAMPLE, encoding="utf-8")
    second = pipeline.ingest(cfg, dry_run=False)

    assert second.deprecated == ["revenue-policy"]
    path = cfg.bundle_root / "revenue-policy.md"
    assert path.exists()
    assert okf.parse(path.read_text(encoding="utf-8-sig"), "revenue-policy").status == "deprecated"
    assert "**Deprecation**" in (cfg.bundle_root / "log.md").read_text(encoding="utf-8-sig")


def test_a_source_is_deprecated_once_not_on_every_later_build(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    (project / "sources" / "revenue-policy.md").unlink()
    (project / "sources" / "other.md").write_text(SAMPLE, encoding="utf-8")
    pipeline.ingest(cfg, dry_run=False)

    monkeypatch.setattr(pipeline, "synthesize", no_model)
    third = pipeline.ingest(cfg, dry_run=False)
    assert third.deprecated == []


# --- stale output ----------------------------------------------------------------


def test_a_renamed_concept_tree_does_not_leave_the_old_one_behind(segmented, monkeypatch):
    cfg = config_module.load(segmented)
    pipeline.ingest(cfg, dry_run=False)
    assert (cfg.bundle_root / "trabajo-practico" / "definicion.md").exists()

    # same source, but the model names the document differently this time — the
    # observed normal case, since ids come from model-written titles
    def renamed_plan(**kwargs):
        outline = fake_plan(**kwargs)
        outline.title = "Trabajo Final"
        return outline

    (segmented / "sources" / "tp.md").write_text(LONG + " otra vez", encoding="utf-8")
    monkeypatch.setattr(pipeline, "plan", renamed_plan)
    second = pipeline.ingest(cfg, dry_run=False)

    tp = next(o for o in second.written if o.uri == "sources/tp.md")
    assert tp.concept_id == "trabajo-final"
    assert not (cfg.bundle_root / "trabajo-practico.md").exists()
    assert not (cfg.bundle_root / "trabajo-practico").exists()
    assert sorted(tp.removed) == [
        "trabajo-practico",
        "trabajo-practico/definicion",
        "trabajo-practico/doctrina",
    ]
    assert second.report is not None and second.report.ok


def test_a_skipped_source_does_not_lose_its_id_to_another_source(project, monkeypatch):
    """Two sources with the same title, and only the later one changed.

    sources/archive/ is discovered first and skipped, so its id is claimed by
    nothing this run — the rebuilt source would take it if the ledger's ids were
    not spoken for up front.
    """
    nested = project / "sources" / "archive"
    nested.mkdir()
    (nested / "revenue-policy.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    first = pipeline.ingest(cfg, dry_run=False)
    before = {o.uri: o.concept_id for o in first.written}
    assert before["sources/archive/revenue-policy.md"] == "revenue-policy"

    (project / "sources" / "revenue-policy.md").write_text(
        SAMPLE + "\nMore.\n", encoding="utf-8"
    )
    second = pipeline.ingest(cfg, dry_run=False)

    assert {o.uri: o.concept_id for o in second.written} == before


# --- human edits (§6.4) ----------------------------------------------------------


def test_a_hand_edited_concept_is_not_overwritten(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    path = cfg.bundle_root / "revenue-policy.md"
    concept = okf.parse(path.read_text(encoding="utf-8-sig"), "revenue-policy")
    concept.body = "# Rule\n\nA human rewrote this by hand."
    okf.write_concept(cfg.bundle_root, concept)

    (project / "sources" / "revenue-policy.md").write_text(SAMPLE + "\nNew.\n", encoding="utf-8")
    second = pipeline.ingest(cfg, dry_run=False)

    assert "A human rewrote this by hand." in path.read_text(encoding="utf-8-sig")
    outcome = next(o for o in second.written if o.uri == "sources/revenue-policy.md")
    assert outcome.protected == ["revenue-policy: edited since ragged last wrote it"]
    proposed = cfg.sidecar_root / "proposed" / "revenue-policy.md"
    assert "Revenue is recognized on delivery" in proposed.read_text(encoding="utf-8-sig")


def test_a_verified_concept_is_protected_even_with_no_ledger_record(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    path = cfg.bundle_root / "revenue-policy.md"
    concept = okf.parse(path.read_text(encoding="utf-8-sig"), "revenue-policy")
    concept.verified = [{"by": "human:juan", "at": "2026-08-05T00:00:00Z"}]
    okf.write_concept(cfg.bundle_root, concept)
    (cfg.sidecar_root / LEDGER_NAME).unlink()

    second = pipeline.ingest(cfg, dry_run=False)
    outcome = next(o for o in second.written if o.uri == "sources/revenue-policy.md")
    assert outcome.protected == ["revenue-policy: carries a `verified` block"]
    reread = okf.parse(path.read_text(encoding="utf-8-sig"), "revenue-policy")
    assert reread.verified == [{"by": "human:juan", "at": "2026-08-05T00:00:00Z"}]


def test_a_protected_concept_is_not_deleted_as_stale(segmented, monkeypatch):
    cfg = config_module.load(segmented)
    pipeline.ingest(cfg, dry_run=False)

    child = cfg.bundle_root / "trabajo-practico" / "definicion.md"
    concept = okf.parse(child.read_text(encoding="utf-8-sig"), "trabajo-practico/definicion")
    concept.verified = [{"by": "human:juan", "at": "2026-08-05T00:00:00Z"}]
    okf.write_concept(cfg.bundle_root, concept)

    def renamed_plan(**kwargs):
        outline = fake_plan(**kwargs)
        outline.title = "Trabajo Final"
        return outline

    (segmented / "sources" / "tp.md").write_text(LONG + " otra vez", encoding="utf-8")
    monkeypatch.setattr(pipeline, "plan", renamed_plan)
    pipeline.ingest(cfg, dry_run=False)

    assert child.exists(), "a concept a human verified must survive a rename"


# --- the ledger itself -----------------------------------------------------------


def test_the_ledger_records_the_body_ragged_wrote(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    ledger = ledger_of(cfg)
    text = (cfg.bundle_root / "revenue-policy.md").read_text(encoding="utf-8-sig")
    on_disk = okf.parse(text, "revenue-policy")
    assert ledger.concepts["revenue-policy"].body_sha256 == body_hash(on_disk.body)
    assert ledger.sources["sources/revenue-policy.md"].concepts == ["revenue-policy"]


def test_a_dry_run_writes_no_ledger(project, monkeypatch):
    def explode(**_):
        raise AssertionError("dry run must not call the model")

    monkeypatch.setattr(pipeline, "synthesize", explode)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=True)
    assert not (cfg.sidecar_root / LEDGER_NAME).exists()


def test_a_failed_synthesis_leaves_the_previous_record_intact(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    before = ledger_of(cfg).sources["sources/revenue-policy.md"]

    def quota_exceeded(**_):
        raise ProviderHTTPError("gemini", 429, "quota")

    (project / "sources" / "revenue-policy.md").write_text(SAMPLE + "\nNew.\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "synthesize", quota_exceeded)
    second = pipeline.ingest(cfg, dry_run=False)

    assert [o.uri for o in second.failed] == ["sources/revenue-policy.md"]
    after = ledger_of(cfg).sources["sources/revenue-policy.md"]
    assert after.content_sha256 == before.content_sha256, "a failure must not record the new hash"
    assert (cfg.bundle_root / "revenue-policy.md").exists()
