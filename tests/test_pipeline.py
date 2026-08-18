from __future__ import annotations

from pathlib import Path

import pytest

from okragged import config as config_module
from okragged import okf, pipeline
from okragged.synth import (
    MAX_CONCEPT_WORDS,
    Outline,
    ProviderHTTPError,
    Section,
    SynthResult,
    WeaveResult,
    anchor_footnote_defs,
    body_defines,
    ensure_footnote_defs,
)

SAMPLE = """\
# Revenue Recognition

Revenue is recognized when the service is delivered, not when invoiced.
Annual prepayments are deferred and released monthly.
"""


def fake_weave(**kwargs) -> WeaveResult:
    """A weave pass that finds nothing worth linking — the default everywhere.

    Stubbed in the fixture rather than per test, because an unstubbed weave
    would reach the network and the suite runs offline.
    """
    return WeaveResult(body_markdown=kwargs["body"], invented=[])


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # the default provider is anthropic; satisfy its pre-flight credential check
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setattr(pipeline, "weave", fake_weave)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "revenue-policy.md").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "okragged.toml").write_text(
        '[bundle]\nroot = "bundle"\nokf_version = "0.2"\nactor = "okragged/test"\n\n'
        '[sources.local]\npath = "sources"\ninclude = ["**/*.md"]\n\n'
        '[types]\nallowed = ["Document", "Policy"]\nallow_new = true\n',
        encoding="utf-8",
    )
    return tmp_path


def fake_synthesize(**kwargs) -> SynthResult:
    source_id = kwargs["source_id"]
    return SynthResult(
        type="Policy",
        title="Revenue Recognition Policy",
        description="When revenue is recognized and how prepayments are deferred.",
        body_markdown=(
            f"# Rule\n\nRevenue is recognized on delivery, not on invoice.[^{source_id}]\n\n"
            f"[^{source_id}]: revenue-policy"
        ),
        tags=["finance"],
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
    )


def test_dry_run_extracts_without_calling_the_model(project, monkeypatch):
    def explode(**_):
        raise AssertionError("dry run must not call the model")

    monkeypatch.setattr(pipeline, "synthesize", explode)
    summary = pipeline.ingest(config_module.load(project), dry_run=True)

    assert [o.uri for o in summary.written] == ["sources/revenue-policy.md"]
    assert not (project / "bundle").exists()


def test_one_file_in_one_conformant_concept_out(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    summary = pipeline.ingest(cfg, dry_run=False)

    assert not summary.failed
    assert [o.concept_id for o in summary.written] == ["revenue-policy"]
    assert summary.report is not None and summary.report.ok, (
        [str(f) for f in summary.report.errors] if summary.report else None
    )

    written = (cfg.bundle_root / "revenue-policy.md").read_text(encoding="utf-8")
    concept = okf.parse(written, "revenue-policy")
    assert concept.type == "Policy"
    assert concept.status == "draft"
    assert concept.generated == {"by": "okragged/test", "at": concept.generated["at"]}
    assert concept.sources[0].id == "revenue-policy"
    assert concept.extra["x_okragged_source_hash"]
    # never fabricate a verification event
    assert concept.verified == []

    index = (cfg.bundle_root / "index.md").read_text(encoding="utf-8")
    assert 'okf_version: "0.2"' in index
    assert "revenue-policy.md" in index
    assert "When revenue is recognized" in index

    log = (cfg.bundle_root / "log.md").read_text(encoding="utf-8")
    assert "**Creation**" in log


def test_new_type_is_reported_not_silently_accepted(project, monkeypatch):
    def novel(**kwargs):
        result = fake_synthesize(**kwargs)
        result.type = "AccountingRule"
        return result

    monkeypatch.setattr(pipeline, "synthesize", novel)
    summary = pipeline.ingest(config_module.load(project), dry_run=False)
    assert [o.new_type for o in summary.written] == ["AccountingRule"]


def test_extraction_failure_does_not_abort_the_run(project, monkeypatch):
    (project / "sources" / "empty.md").write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    summary = pipeline.ingest(config_module.load(project), dry_run=False)

    assert [o.uri for o in summary.failed] == ["sources/empty.md"]
    assert [o.concept_id for o in summary.written] == ["revenue-policy"]


def test_a_non_200_does_not_abort_the_run(project, monkeypatch):
    # sorts after revenue-policy.md, so the quota error lands on the second file
    (project / "sources" / "zz-quota.md").write_text(SAMPLE, encoding="utf-8")

    def quota_exceeded_on_the_second(**kwargs):
        if kwargs["source_uri"].endswith("zz-quota.md"):
            raise ProviderHTTPError("gemini", 429, "You exceeded your current quota")
        return fake_synthesize(**kwargs)

    monkeypatch.setattr(pipeline, "synthesize", quota_exceeded_on_the_second)
    cfg = config_module.load(project)
    summary = pipeline.ingest(cfg, dry_run=False)

    assert [o.concept_id for o in summary.written] == ["revenue-policy"]
    assert [o.uri for o in summary.failed] == ["sources/zz-quota.md"]
    assert "HTTP 429" in summary.failed[0].error

    # the concept that succeeded is a bundle, not an orphan file
    assert "revenue-policy.md" in (cfg.bundle_root / "index.md").read_text(encoding="utf-8")
    assert (cfg.bundle_root / "log.md").exists()
    assert (cfg.project_root / "okf-base.yaml").exists()
    assert summary.report is not None and summary.report.ok


def test_an_interrupted_run_leaves_a_bundle_of_the_sources_that_committed(project, monkeypatch):
    """The commit boundary is the source, not the run (§6.6).

    The source this run finished is published; the one it was in the middle of is
    not, and nothing half of a source's emit is on disk — that is the window the
    pending record fences, because ids come from model-written titles and half an
    emit is a mix of two namings rather than a subset. `tests/test_writers.py`
    covers what the next run pays for.
    """
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    (project / "sources" / "zz-one.md").write_text(SAMPLE + "\nOne.\n", encoding="utf-8")
    (project / "sources" / "zz-two.md").write_text(SAMPLE + "\nTwo.\n", encoding="utf-8")

    def interrupt_the_second_new_source(**kwargs):
        if kwargs["source_uri"].endswith("zz-two.md"):
            raise KeyboardInterrupt
        return fake_synthesize(**kwargs)

    monkeypatch.setattr(pipeline, "synthesize", interrupt_the_second_new_source)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    assert (cfg.bundle_root / "zz-one.md").exists()  # committed by its own emit
    assert not (cfg.bundle_root / "zz-two.md").exists()  # was still in flight
    index = (cfg.bundle_root / "index.md").read_text(encoding="utf-8-sig")
    assert "zz-one.md" in index and "zz-two.md" not in index
    assert okf.validate(cfg.bundle_root).ok


def test_colliding_titles_get_distinct_concept_ids(project, monkeypatch):
    nested = project / "sources" / "archive"
    nested.mkdir()
    (nested / "revenue-policy.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    summary = pipeline.ingest(config_module.load(project), dry_run=False)

    ids = sorted(o.concept_id for o in summary.written)
    assert ids == ["revenue-policy", "revenue-policy-2"]


# --- segmentation ---------------------------------------------------------------

LONG = "palabra " * (MAX_CONCEPT_WORDS + 100)


def fake_plan(**kwargs) -> Outline:
    return Outline(
        type="Document",
        title="Trabajo Practico",
        description="Un TP que cubre dos temas distintos.",
        overview="Cubre la definicion doctrinaria y el marco normativo.",
        tags=["derecho"],
        sections=[
            Section("Definicion", "texto de la definicion"),
            Section("Doctrina", "texto de la doctrina"),
        ],
        input_tokens=10,
        output_tokens=5,
    )


def fake_section_synthesize(**kwargs) -> SynthResult:
    """Whole-document calls keep the M1 fake; section calls answer per section."""
    section = kwargs.get("section")
    if section is None:
        return fake_synthesize(**kwargs)
    source_id = kwargs["source_id"]
    return SynthResult(
        type="Document",
        title=section.section_title,
        description=f"Lo que dice la seccion {section.section_title}.",
        body_markdown=f"Contenido sustantivo.[^{source_id}]\n\n[^{source_id}]: tp",
        tags=[],
        input_tokens=50,
        output_tokens=25,
        cache_read_tokens=0,
    )


@pytest.fixture
def segmented(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (project / "sources" / "tp.md").write_text(LONG, encoding="utf-8")
    monkeypatch.setattr(pipeline, "plan", fake_plan)
    monkeypatch.setattr(pipeline, "synthesize", fake_section_synthesize)
    return project


def test_a_long_source_becomes_a_parent_plus_one_concept_per_section(segmented):
    cfg = config_module.load(segmented)
    summary = pipeline.ingest(cfg, dry_run=False)

    tp = next(o for o in summary.written if o.uri == "sources/tp.md")
    assert tp.concept_id == "trabajo-practico"
    assert tp.child_ids == ["trabajo-practico/definicion", "trabajo-practico/doctrina"]
    assert tp.concept_count == 3
    assert (cfg.bundle_root / "trabajo-practico.md").exists()
    assert (cfg.bundle_root / "trabajo-practico" / "definicion.md").exists()
    assert (cfg.bundle_root / "trabajo-practico" / "doctrina.md").exists()


def test_a_short_source_in_the_same_run_is_still_one_concept(segmented):
    summary = pipeline.ingest(config_module.load(segmented), dry_run=False)
    short = next(o for o in summary.written if o.uri == "sources/revenue-policy.md")
    assert short.concept_id == "revenue-policy"
    assert short.child_ids == []


def test_a_segmented_bundle_is_conformant(segmented):
    cfg = config_module.load(segmented)
    summary = pipeline.ingest(cfg, dry_run=False)
    assert summary.report is not None and summary.report.ok, (
        [str(f) for f in summary.report.errors] if summary.report else None
    )
    # W4 would fire for a subdirectory with no index.md
    assert not [w for w in summary.report.warnings if w.code == "W4"]


def test_the_child_index_carries_no_frontmatter(segmented):
    cfg = config_module.load(segmented)
    pipeline.ingest(cfg, dry_run=False)
    index = (cfg.bundle_root / "trabajo-practico" / "index.md").read_text(encoding="utf-8-sig")
    assert not index.startswith("---")  # E3: only the bundle root may have frontmatter
    assert "definicion.md" in index and "doctrina.md" in index


def test_the_root_index_lists_the_parent_not_every_child(segmented):
    cfg = config_module.load(segmented)
    pipeline.ingest(cfg, dry_run=False)
    root = (cfg.bundle_root / "index.md").read_text(encoding="utf-8-sig")
    assert "trabajo-practico.md" in root
    assert "definicion.md" not in root  # progressive disclosure


def test_the_parent_links_down_to_each_child(segmented):
    cfg = config_module.load(segmented)
    pipeline.ingest(cfg, dry_run=False)
    parent = (cfg.bundle_root / "trabajo-practico.md").read_text(encoding="utf-8-sig")
    # labelled by the child's title, not by its slug run through title-case
    assert "[Definicion](/trabajo-practico/definicion.md)" in parent
    assert "[Doctrina](/trabajo-practico/doctrina.md)" in parent


def test_one_failed_section_does_not_lose_the_others(segmented, monkeypatch):
    def fail_the_second(**kwargs):
        section = kwargs.get("section")
        if section is not None and section.section_title == "Doctrina":
            raise ProviderHTTPError("gemini", 429, "quota")
        return fake_section_synthesize(**kwargs)

    monkeypatch.setattr(pipeline, "synthesize", fail_the_second)
    cfg = config_module.load(segmented)
    summary = pipeline.ingest(cfg, dry_run=False)

    tp = next(o for o in summary.written if o.uri == "sources/tp.md")
    assert tp.child_ids == ["trabajo-practico/definicion"]
    assert len(tp.section_errors) == 1 and "Doctrina" in tp.section_errors[0]
    assert summary.report is not None and summary.report.ok


def test_every_section_failing_fails_the_document(segmented, monkeypatch):
    def fail_all(**kwargs):
        if kwargs.get("section") is not None:
            raise ProviderHTTPError("gemini", 429, "quota")
        return fake_section_synthesize(**kwargs)

    monkeypatch.setattr(pipeline, "synthesize", fail_all)
    summary = pipeline.ingest(config_module.load(segmented), dry_run=False)

    tp = next(o for o in summary.outcomes if o.uri == "sources/tp.md")
    assert tp.concept_id is None
    assert "all 2 section(s) failed" in tp.error


def test_an_unmatched_marker_is_surfaced_in_the_summary(segmented, monkeypatch):
    def plan_with_a_dropped_section(**kwargs):
        outline = fake_plan(**kwargs)
        outline.unmatched = ["Bibliografia"]
        return outline

    monkeypatch.setattr(pipeline, "plan", plan_with_a_dropped_section)
    summary = pipeline.ingest(config_module.load(segmented), dry_run=False)
    tp = next(o for o in summary.written if o.uri == "sources/tp.md")
    assert tp.dropped_sections == ["Bibliografia"]


def test_missing_footnote_definition_is_appended():
    body = "A claim.[^src]\n"
    fixed = ensure_footnote_defs(body, "src", "Handbook")
    assert fixed.endswith("[^src]: Handbook")


def test_existing_footnote_definition_is_left_alone():
    body = "A claim.[^src]\n\n[^src]: Already defined"
    assert ensure_footnote_defs(body, "src", "Handbook") == body

def test_a_definition_left_mid_line_gets_its_own_line():
    body = "A claim.[^src] Another.[^src] [^src]: Handbook"
    assert anchor_footnote_defs(body) == "A claim.[^src] Another.[^src]\n\n[^src]: Handbook"


def test_a_definition_already_starting_its_line_is_left_alone():
    body = "A claim.[^src]\n\n[^src]: Handbook"
    assert anchor_footnote_defs(body) == body


def test_a_reference_defined_only_mid_line_is_not_read_as_defined():
    body = "A claim.[^src] [^src]: Handbook"
    assert not body_defines(body, "src")
    assert body_defines(anchor_footnote_defs(body), "src")
