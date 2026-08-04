from __future__ import annotations

from pathlib import Path

import pytest

from ragged import config as config_module
from ragged import okf, pipeline
from ragged.synth import ProviderHTTPError, SynthResult, ensure_footnote_defs

SAMPLE = """\
# Revenue Recognition

Revenue is recognized when the service is delivered, not when invoiced.
Annual prepayments are deferred and released monthly.
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # the default provider is anthropic; satisfy its pre-flight credential check
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "revenue-policy.md").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "ragged.toml").write_text(
        '[bundle]\nroot = "bundle"\nokf_version = "0.2"\nactor = "ragged/test"\n\n'
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
    assert concept.generated == {"by": "ragged/test", "at": concept.generated["at"]}
    assert concept.sources[0].id == "revenue-policy"
    assert concept.extra["x_ragged_source_hash"]
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


def test_an_interrupted_run_still_leaves_a_readable_bundle(project, monkeypatch):
    (project / "sources" / "zz-interrupted.md").write_text(SAMPLE, encoding="utf-8")

    def interrupt_on_the_second(**kwargs):
        if kwargs["source_uri"].endswith("zz-interrupted.md"):
            raise KeyboardInterrupt
        return fake_synthesize(**kwargs)

    monkeypatch.setattr(pipeline, "synthesize", interrupt_on_the_second)
    cfg = config_module.load(project)
    with pytest.raises(KeyboardInterrupt):
        pipeline.ingest(cfg, dry_run=False)

    assert (cfg.bundle_root / "revenue-policy.md").exists()
    assert "revenue-policy.md" in (cfg.bundle_root / "index.md").read_text(encoding="utf-8")
    assert okf.validate(cfg.bundle_root).ok


def test_colliding_titles_get_distinct_concept_ids(project, monkeypatch):
    nested = project / "sources" / "archive"
    nested.mkdir()
    (nested / "revenue-policy.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    summary = pipeline.ingest(config_module.load(project), dry_run=False)

    ids = sorted(o.concept_id for o in summary.written)
    assert ids == ["revenue-policy", "revenue-policy-2"]


def test_missing_footnote_definition_is_appended():
    body = "A claim.[^src]\n"
    fixed = ensure_footnote_defs(body, "src", "Handbook")
    assert fixed.endswith("[^src]: Handbook")


def test_existing_footnote_definition_is_left_alone():
    body = "A claim.[^src]\n\n[^src]: Already defined"
    assert ensure_footnote_defs(body, "src", "Handbook") == body
