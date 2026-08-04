from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from ragged import okf


def test_manifest_has_the_keys_okflint_requires(tmp_path):
    path = okf.write_manifest(tmp_path, "bundle", "Test Bundle")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert data["base"]["name"] == "Test Bundle"
    assert data["base"]["roots"] == [{"path": "bundle", "exclude_patterns": []}]
    # okflint raises ManifestError without both of these keys
    assert set(data["base"]["reserved_files"]) == {"index", "log"}
    # the spec permits broken links; they must never fail the gate
    assert data["hygiene"]["broken_links"] == "warn"
    # okflint 0.3.1 warns on any okf_version other than "0.1" — omit it
    assert "okf_version" not in data


def test_validate_skips_okflint_without_a_manifest(tmp_path):
    bundle = tmp_path / "bundle"
    okf.write_concept(
        bundle,
        okf.Concept(concept_id="a", type="Document", title="A", description="d", body="x"),
    )
    report = okf.validate(bundle, use_okflint=True)
    assert report.okflint_exit is None
    if shutil.which("okflint"):
        assert okf.MANIFEST_NAME in report.okflint_note
    else:
        assert "not installed" in report.okflint_note


@pytest.mark.skipif(shutil.which("okflint") is None, reason="okflint not on PATH")
def test_okflint_agrees_with_the_builtin_checks(tmp_path):
    bundle = tmp_path / "bundle"
    okf.write_concept(
        bundle,
        okf.Concept(
            concept_id="a",
            type="Document",
            title="A",
            description="d",
            body="See [missing](/nope.md).",
            generated={"by": "ragged/test", "at": "2026-08-04T00:00:00Z"},
        ),
    )
    okf.write_root_index(bundle, "0.2", "T", [("a", "d")])
    okf.append_log(bundle, "2026-08-04", ["**Creation**: a."])
    okf.write_manifest(tmp_path, "bundle", "T")

    report = okf.validate(bundle, use_okflint=True)
    assert report.okflint_exit == 0, report.okflint_output
    assert report.ok
    # both flag the same broken link, neither treats it as an error
    assert any(f.code == "W2" for f in report.warnings)
    assert "nope.md" in report.okflint_output


def test_okflint_output_survives_non_cp1252_bytes(tmp_path, monkeypatch):
    """Regression: text=True inherits the Windows codepage and crashes on emoji."""
    if shutil.which("okflint") is None:
        pytest.skip("okflint not on PATH")
    bundle = tmp_path / "bundle"
    okf.write_concept(
        bundle,
        okf.Concept(concept_id="a", type="Document", title="A", description="d",
                    body="See [missing](/nope.md)."),
    )
    okf.write_manifest(tmp_path, "bundle", "T")
    report = okf.validate(bundle, use_okflint=True)
    assert isinstance(report.okflint_output, str)
    assert report.okflint_exit is not None
