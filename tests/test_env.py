"""`.env` loading. Regression cover for the BOM trap."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ragged import cli

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    (tmp_path / "sources").mkdir()
    (tmp_path / "ragged.toml").write_text('[synth]\nprovider = "gemini"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _stub_reachable(monkeypatch, models):
    from ragged.synth.providers import gemini

    monkeypatch.setattr(gemini, "list_models", lambda: models)


def test_env_with_a_bom_still_loads(project, monkeypatch):
    """Notepad / VS Code / Set-Content -Encoding utf8 all write a BOM."""
    (project / ".env").write_text("GEMINI_API_KEY=abc\n", encoding="utf-8-sig")
    _stub_reachable(monkeypatch, ["gemini-2.5-pro"])

    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "credential     present" in result.output


def test_env_without_a_bom_loads(project, monkeypatch):
    (project / ".env").write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    _stub_reachable(monkeypatch, ["gemini-2.5-pro"])

    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "credential     present" in result.output


def test_doctor_reports_a_missing_credential(project):
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "credential     missing" in result.output
    assert "GEMINI_API_KEY" in result.output


def test_doctor_reports_the_embedding_provider_too_not_just_the_synth_one(project, monkeypatch):
    """`index` has its own provider and its own credential (DESIGN §6.8)."""
    (project / ".env").write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    _stub_reachable(monkeypatch, ["gemini-2.5-pro"])

    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "embeddings: gemini" in result.output
    assert "gemini-embedding-001" in result.output
    assert "0.82" in result.output  # tau, so a guess is visible as a guess
    assert "graph.json" in result.output


def test_doctor_reports_the_embedding_half_even_when_the_synth_half_fails(project):
    """A missing synth key must not hide what `index` would do, or vice versa."""
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "embeddings: gemini" in result.output
    assert "`ragged index` will not run" in result.output


def test_doctor_lists_alternatives_when_the_model_is_unavailable(project, monkeypatch):
    (project / ".env").write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    _stub_reachable(monkeypatch, ["gemini-9-turbo", "gemini-9-flash"])

    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "NOT in the list" in result.output
    assert "gemini-9-turbo" in result.output


def test_doctor_reports_an_unreachable_provider(project, monkeypatch):
    from ragged.synth.providers import gemini

    (project / ".env").write_text("GEMINI_API_KEY=bad\n", encoding="utf-8")

    def boom():
        raise RuntimeError("401 API key not valid")

    monkeypatch.setattr(gemini, "list_models", boom)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "reachable      NO" in result.output
    assert "API key not valid" in result.output


def test_build_without_a_credential_exits_2_and_points_at_env(project):
    (project / "sources" / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["build"])
    assert result.exit_code == 2
    assert "GEMINI_API_KEY" in result.output
    assert ".env" in result.output


def test_dry_run_needs_no_credential(project):
    (project / "sources" / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["build", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would write 1 concept" in result.output
