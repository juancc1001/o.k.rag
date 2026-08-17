"""Driving okragged from Python: the public surface, and the two things it changed.

The surface itself is thin — every function here delegates to the one the CLI
calls — so what is worth testing is the contract around it: that importing the
package is silent and cheap, that a library `build` behaves as `okragged build`
does while neither of them registers an MCP server, and that a run reports
progress while it is running rather than only when it is over.

The model is stubbed throughout, as everywhere else in this suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import okragged
from okragged import cli, pipeline
from okragged.synth import MAX_CONCEPT_WORDS, Outline, Section, SynthResult, WeaveResult

SHORT = """\
# Revenue Recognition

Revenue is recognized when the service is delivered, not when invoiced.
"""

LONG = "palabra " * (MAX_CONCEPT_WORDS + 100)

runner = CliRunner()


def fake_synthesize(**kwargs) -> SynthResult:
    section = kwargs.get("section")
    title = section.section_title if section is not None else "Revenue Recognition Policy"
    source_id = kwargs["source_id"]
    return SynthResult(
        type="Policy",
        title=title,
        description=f"What {title} says.",
        body_markdown=f"Substance.[^{source_id}]\n\n[^{source_id}]: {source_id}",
        tags=["finance"],
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
    )


def fake_plan(**kwargs) -> Outline:
    return Outline(
        type="Document",
        title="Trabajo Practico",
        description="Dos temas distintos.",
        overview="Cubre la definicion y la doctrina.",
        tags=[],
        sections=[
            Section("Definicion", "texto de la definicion"),
            Section("Doctrina", "texto de la doctrina"),
        ],
        input_tokens=1,
        output_tokens=1,
    )


def fake_weave(**kwargs) -> WeaveResult:
    return WeaveResult(body_markdown=kwargs["body"], invented=[])


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline, "plan", fake_plan)
    monkeypatch.setattr(pipeline, "weave", fake_weave)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "revenue-policy.md").write_text(SHORT, encoding="utf-8")
    (tmp_path / "okragged.toml").write_text(
        '[bundle]\nactor = "okragged/test"\n\n[types]\nallowed = ["Policy", "Document"]\n',
        encoding="utf-8",
    )
    return tmp_path


def _child(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


# --- the import has to be silent, or the library is unusable in a pipe ------------


def test_importing_the_package_prints_nothing_at_all():
    """markitdown prints an ffmpeg warning when imported, and `extract/` imports it.

    A library that writes to somebody else's stderr in order to be imported is
    broken — and for the MCP server, stdout is the protocol.
    """
    proc = _child("import okragged")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_importing_the_package_pulls_in_neither_extraction_nor_a_provider():
    heavy = ("okragged.extract", "okragged.pipeline", "okragged.synth", "markitdown", "numpy")
    proc = _child(
        "import sys, json, okragged; "
        f"print(json.dumps([m for m in {heavy!r} if m in sys.modules]))"
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == []


def test_the_two_re_exported_names_resolve_and_nothing_else_does():
    from okragged.config import Config
    from okragged.pipeline import Progress

    assert okragged.Config is Config
    assert okragged.Progress is Progress
    with pytest.raises(AttributeError):
        okragged.Bundle  # noqa: B018 - a bundle is a directory, not an object (§2)


def test_every_public_name_is_discoverable_and_nothing_else_is():
    """A name reached only through `__getattr__` is absent from `dir()` unless
    `__dir__` says otherwise, so two of the twelve would not complete; and this
    module's own `Path`/`Any` imports would list as though they were surface."""
    assert dir(okragged) == sorted(okragged.__all__)
    for name in okragged.__all__:
        assert getattr(okragged, name) is not None
    for leaked in ("Path", "Any", "TYPE_CHECKING", "annotations"):
        assert leaked not in dir(okragged)


# --- configure -------------------------------------------------------------------


def test_configure_reads_the_projects_toml(project):
    cfg = okragged.configure(project)
    assert cfg.project_root == project
    assert cfg.bundle.actor == "okragged/test"


def test_configure_overrides_a_section_in_memory_without_touching_the_toml(project):
    cfg = okragged.configure(project, synth={"provider": "gemini", "max_tokens": 4000})
    assert (cfg.synth.provider, cfg.synth.max_tokens) == ("gemini", 4000)
    assert cfg.bundle.actor == "okragged/test"  # the rest of the file still applies
    assert "gemini" not in (project / "okragged.toml").read_text(encoding="utf-8-sig")


def test_a_library_call_reads_no_dotenv(project, monkeypatch):
    """Credential discovery is the caller's business; `cli.py` does it because it is
    an entry point for a human."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    (project / ".env").write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    cfg = okragged.configure(project, synth={"provider": "gemini"})

    with pytest.raises(pipeline.MissingCredential):
        okragged.build(cfg)


# --- the contract, and what a build will not write -------------------------------


def test_a_library_build_regenerates_the_contract_like_the_cli_does(project):
    """No library-versus-CLI asymmetry: what justified one was `.mcp.json`, and a
    build no longer writes it either way."""
    summary = okragged.build(project)

    assert summary.report is not None and summary.report.ok, (
        [str(f) for f in summary.report.errors] if summary.report else None
    )
    assert (project / "bundle" / "revenue-policy.md").exists()
    assert (project / "agent" / "AGENTS.md").exists()


def test_no_build_registers_an_mcp_server(project, monkeypatch):
    """Neither entry point may edit the machine's MCP config. It is not derived
    from the bundle, and it is shared with every other server registered in it."""
    okragged.build(project)
    assert not (project / ".mcp.json").exists()

    monkeypatch.chdir(project)
    assert runner.invoke(cli.app, ["build"]).exit_code == 0
    assert not (project / ".mcp.json").exists()


def test_write_contract_is_how_a_caller_asks_for_the_contract_alone(project):
    okragged.build(project, contract=False)
    assert not (project / "agent").exists()

    written = okragged.write_contract(project)
    assert [p.name for p in written] == ["AGENTS.md"]
    assert not (project / ".mcp.json").exists()


def test_register_is_the_only_call_that_writes_the_mcp_config(project):
    okragged.build(project)
    path = okragged.register(project)

    assert path == project / ".mcp.json"
    entry = json.loads(path.read_text(encoding="utf-8-sig"))
    assert entry["mcpServers"]["okragged"] == {"command": "okragged", "args": ["mcp"]}


def test_a_library_build_writes_no_contract_when_told_not_to(project):
    okragged.build(project, contract=False)
    assert not (project / "agent").exists()


def test_okragged_build_still_regenerates_the_contract(project, monkeypatch):
    """§10 depends on this: the contract can never describe a bundle that no longer
    exists, so the CLI regenerates it on every build."""
    monkeypatch.chdir(project)
    result = runner.invoke(cli.app, ["build"])

    assert result.exit_code == 0, result.output
    assert (project / "agent" / "AGENTS.md").exists()


def test_a_dry_run_from_python_writes_nothing_and_calls_no_model(project, monkeypatch):
    def explode(**_):
        raise AssertionError("a dry run must not call the model")

    monkeypatch.setattr(pipeline, "synthesize", explode)
    summary = okragged.build(project, dry_run=True)

    assert [o.uri for o in summary.written] == ["sources/revenue-policy.md"]
    assert not (project / "bundle").exists()


# --- progress -------------------------------------------------------------------


@pytest.fixture
def segmented(project: Path) -> Path:
    (project / "sources" / "tp.md").write_text(LONG, encoding="utf-8")
    return project


def test_a_run_reports_each_stage_as_it_reaches_it(segmented):
    events: list[pipeline.Progress] = []
    okragged.build(segmented, progress=events.append)

    stages = [e.stage for e in events]
    assert set(stages) <= set(pipeline.STAGES)
    assert stages[0] == pipeline.DISCOVER
    assert stages[-2:] == [pipeline.FINALIZE, pipeline.VALIDATE]
    for stage in (pipeline.EXTRACT, pipeline.PLAN, pipeline.SYNTHESIZE, pipeline.WEAVE):
        assert stage in stages


def test_a_progress_event_identifies_the_unit_it_is_about(segmented):
    events: list[pipeline.Progress] = []
    okragged.build(segmented, progress=events.append)

    sections = [e for e in events if e.stage == pipeline.SYNTHESIZE and e.total == 2]
    assert [(e.unit, e.index, e.detail) for e in sections] == [
        ("sources/tp.md", 1, "Definicion"),
        ("sources/tp.md", 2, "Doctrina"),
    ]
    # weave reports concept ids, not source uris: it runs per concept, after emit
    woven = [e for e in events if e.stage == pipeline.WEAVE]
    assert [e.unit for e in woven] == [
        "revenue-policy",
        "trabajo-practico",
        "trabajo-practico/definicion",
        "trabajo-practico/doctrina",
    ]
    assert {e.total for e in woven} == {4}


def test_a_source_reports_finished_once_however_it_finished(project):
    events: list[pipeline.Progress] = []
    okragged.build(project, progress=events.append)
    assert [(e.unit, e.detail) for e in events if e.stage == pipeline.SOURCE] == [
        ("sources/revenue-policy.md", "1 concept(s)")
    ]

    again: list[pipeline.Progress] = []
    okragged.build(project, progress=again.append)
    assert [(e.unit, e.detail) for e in again if e.stage == pipeline.SOURCE] == [
        ("sources/revenue-policy.md", "unchanged")
    ]


def test_a_progress_event_cannot_be_mutated_by_whoever_receives_it(project):
    import dataclasses

    events: list[pipeline.Progress] = []
    okragged.build(project, progress=events.append)
    with pytest.raises(dataclasses.FrozenInstanceError):
        events[0].stage = "rewritten"


def test_a_reporter_that_raises_does_not_fail_a_run_already_paid_for(project):
    def hostile(event: pipeline.Progress) -> None:
        raise RuntimeError("the caller's logging is broken")

    summary = okragged.build(project, progress=hostile)
    assert summary.report is not None and summary.report.ok
    assert (project / "bundle" / "revenue-policy.md").exists()


# --- the read side, reached through the package ----------------------------------


def test_the_agent_operations_take_a_path_or_a_config_interchangeably(project):
    okragged.build(project)
    cfg = okragged.configure(project)

    by_path = okragged.search(project, "revenue recognized", k=3)
    by_config = okragged.search(cfg, "revenue recognized", k=3)
    assert [h.concept_id for h in by_path.hits] == [h.concept_id for h in by_config.hits]
    assert by_path.hits[0].concept_id == "revenue-policy"

    assert okragged.read_concept(project, "revenue-policy").startswith("---")
    assert okragged.check(cfg).ok
    assert okragged.lint(project).conformant
