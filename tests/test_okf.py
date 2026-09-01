from __future__ import annotations

from okrag import okf
from okrag.okf.concept import Concept, Source, parse, render, slugify


def make_concept(**overrides) -> Concept:
    defaults = dict(
        concept_id="metrics/mrr",
        type="Metric",
        title="Monthly Recurring Revenue",
        description="Sum of all active subscription revenue normalized to monthly.",
        body="# Definition\n\nSum of active subscriptions.[^src]\n\n[^src]: Finance handbook",
        sources=[Source(id="src", resource="https://example.com/handbook", title="Handbook")],
        generated={"by": "okrag/0.1.0", "at": "2026-08-04T10:00:00Z"},
        status="draft",
        extra={"x_okrag_source_hash": "abc123"},
    )
    return Concept(**{**defaults, **overrides})


def test_render_starts_with_frontmatter_and_has_type():
    text = render(make_concept())
    assert text.startswith("---\n")
    assert "\ntype: Metric\n" in text
    # body is separated from frontmatter by a blank line
    assert "\n---\n\n# Definition" in text


def test_round_trip_preserves_producer_extensions():
    original = make_concept()
    parsed = parse(render(original), "metrics/mrr")
    assert parsed.type == original.type
    assert parsed.title == original.title
    assert parsed.description == original.description
    assert parsed.body == original.body
    assert parsed.extra["x_okrag_source_hash"] == "abc123"
    assert parsed.sources[0].id == "src"
    assert parsed.generated == original.generated


def test_parse_rejects_missing_frontmatter():
    try:
        parse("# Just a heading\n", "loose")
    except ValueError as exc:
        assert "frontmatter" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_parse_rejects_empty_type():
    try:
        parse("---\ntype: '  '\n---\n\nbody\n", "bad")
    except ValueError as exc:
        assert "type" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_bare_verified_mapping_is_read_as_one_element_list():
    text = "---\ntype: Metric\nverified: {by: 'human:jc', at: '2026-08-04T09:00:00Z'}\n---\n\nbody\n"
    parsed = parse(text, "m")
    assert parsed.verified == [{"by": "human:jc", "at": "2026-08-04T09:00:00Z"}]


def test_slugify_keeps_nesting_and_drops_junk():
    assert slugify("Q3 Report — Final (v2)") == "q3-report-final-v2"
    assert slugify("Reports/Q3 Summary") == "reports/q3-summary"


def test_slugify_folds_accents_instead_of_deleting_them():
    """Stripping non-ASCII turned `informáticos` into `informticos`."""
    assert slugify("Delitos informáticos") == "delitos-informaticos"
    assert slugify("Clasificación y teoría") == "clasificacion-y-teoria"
    assert slugify("Año, niño, ñandú") == "ano-nino-nandu"
    assert slugify("Protección/Tipificación") == "proteccion/tipificacion"


def test_write_concept_and_root_index(tmp_path):
    bundle = tmp_path / "bundle"
    okf.write_concept(bundle, make_concept())
    assert (bundle / "metrics" / "mrr.md").exists()

    okf.write_indexes(bundle, "0.2", "Test Bundle")
    index = (bundle / "index.md").read_text(encoding="utf-8")
    assert index.startswith('---\nokf_version: "0.2"\n---')
    assert "# Test Bundle" in index
    # the root points at the subdirectory rather than pretending to be the whole map
    assert "- [Metrics](./metrics/index.md) - 1 concept(s)" in index

    nested = (bundle / "metrics" / "index.md").read_text(encoding="utf-8")
    assert not nested.startswith("---")  # E3
    assert "- [Monthly Recurring Revenue](./mrr.md) - Sum of all active" in nested


def test_append_log_keeps_dates_newest_first(tmp_path):
    bundle = tmp_path / "bundle"
    okf.append_log(bundle, "2026-08-01", ["**Creation**: first."])
    okf.append_log(bundle, "2026-08-04", ["**Update**: second."])
    log = (bundle / "log.md").read_text(encoding="utf-8")
    assert log.index("## 2026-08-04") < log.index("## 2026-08-01")
    assert "- **Update**: second." in log


def test_append_log_merges_into_existing_date(tmp_path):
    bundle = tmp_path / "bundle"
    okf.append_log(bundle, "2026-08-04", ["**Creation**: a."])
    okf.append_log(bundle, "2026-08-04", ["**Creation**: b."])
    log = (bundle / "log.md").read_text(encoding="utf-8")
    assert log.count("## 2026-08-04") == 1
    assert "- **Creation**: a." in log and "- **Creation**: b." in log


def _bundle(tmp_path):
    bundle = tmp_path / "bundle"
    okf.write_concept(bundle, make_concept())
    okf.write_indexes(bundle, "0.2", "T")
    okf.append_log(bundle, "2026-08-04", ["**Creation**: x."])
    return bundle


def test_validate_accepts_conformant_bundle(tmp_path):
    report = okf.validate(_bundle(tmp_path), use_okflint=False)
    assert report.ok, [str(f) for f in report.errors]
    assert report.concepts == 1


def test_validate_flags_frontmatter_in_non_root_index(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "metrics" / "index.md").write_text("---\nfoo: bar\n---\n\n# Metrics\n", encoding="utf-8")
    report = okf.validate(bundle, use_okflint=False)
    assert [f.code for f in report.errors] == ["E3"]


def test_validate_flags_frontmatter_in_log(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "log.md").write_text("---\nx: 1\n---\n\n# Update Log\n", encoding="utf-8")
    report = okf.validate(bundle, use_okflint=False)
    assert any(f.code == "E3" and f.path == "log.md" for f in report.errors)


def test_validate_flags_missing_type_as_error(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "orphan.md").write_text("---\ntitle: no type\n---\n\nbody\n", encoding="utf-8")
    report = okf.validate(bundle, use_okflint=False)
    assert any(f.code == "E2" for f in report.errors)
    assert not report.ok


def test_broken_link_is_a_warning_not_an_error(tmp_path):
    bundle = tmp_path / "bundle"
    okf.write_concept(
        bundle,
        make_concept(body="See [churn](/metrics/churn.md), which is not written yet."),
    )
    report = okf.validate(bundle, use_okflint=False)
    assert report.ok
    assert any(f.code == "W2" for f in report.warnings)


def test_resolved_link_produces_no_warning(tmp_path):
    bundle = tmp_path / "bundle"
    okf.write_concept(bundle, make_concept(body="See [churn](/metrics/churn.md)."))
    okf.write_concept(bundle, make_concept(concept_id="metrics/churn", title="Churn"))
    report = okf.validate(bundle, use_okflint=False)
    assert not [f for f in report.warnings if f.code == "W2"]


def test_relative_link_resolves_from_concept_directory(tmp_path):
    bundle = tmp_path / "bundle"
    okf.write_concept(bundle, make_concept(body="See [churn](./churn.md)."))
    okf.write_concept(bundle, make_concept(concept_id="metrics/churn", title="Churn"))
    report = okf.validate(bundle, use_okflint=False)
    assert not [f for f in report.warnings if f.code == "W2"]


def test_missing_recommended_fields_are_warnings(tmp_path):
    bundle = tmp_path / "bundle"
    okf.write_concept(bundle, make_concept(title=None, description=None, generated=None))
    report = okf.validate(bundle, use_okflint=False)
    assert report.ok
    codes = sorted(f.code for f in report.warnings)
    assert codes.count("W1") == 2
    assert "W3" in codes
