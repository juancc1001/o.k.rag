"""`okragged mcp --transport http` (DESIGN.md §13 M8, criteria 1 and 4): the
CLI's own validation, kept offline by monkeypatching `agent.server.main`
rather than letting it actually bind a socket - same style as
`tests/test_cli_view.py`.

The cases that are *not* monkeypatched are the missing-config paths:
`MissingAuthConfig`, from either `require_http_auth_config` or (for
`--write --spend` together) `require_http_spend_config`, is raised before
`agent.server.main` is ever reached, so exercising them for real needs no
extra and no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from okragged import cli

runner = CliRunner()

MCP_TOML = """\
[mcp]
issuer_url = "https://issuer.example.com"
resource_server_url = "https://mcp.example.com"
jwks_url = "https://issuer.example.com/.well-known/jwks.json"
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "bundle").mkdir()
    return tmp_path


def write_mcp_config(project: Path) -> None:
    (project / "okragged.toml").write_text(MCP_TOML, encoding="utf-8", newline="\n")


def served(project: Path, monkeypatch, args: list[str]):
    from okragged.agent import server

    seen: list[tuple[Path, dict]] = []
    monkeypatch.setattr(server, "main", lambda root, **modes: seen.append((root, modes)))
    result = runner.invoke(cli.app, args)
    return result, seen


# --- validation, before main is ever called ---------------------------------------


def test_an_unknown_transport_is_refused(project, monkeypatch):
    result, seen = served(
        project, monkeypatch, ["mcp", "--project", str(project), "--transport", "carrier-pigeon"]
    )
    assert result.exit_code == 2
    assert seen == []
    assert "carrier-pigeon" in result.stderr


@pytest.mark.parametrize("flag", ["--write", "--spend"])
def test_http_transport_allows_write_or_spend_alone(project, monkeypatch, flag):
    """Neither needs a spend scope or a rate limit on its own: plain --write only
    records a source (concurrency-safe since M8 criterion 3), and --spend alone
    only lets `search` embed a query on the host's key - unchanged since M6."""
    write_mcp_config(project)
    result, seen = served(
        project, monkeypatch, ["mcp", "--project", str(project), "--transport", "http", flag]
    )
    assert result.exit_code == 0, result.output
    assert seen != []


def test_http_transport_refuses_write_and_spend_together_with_no_spend_config(project):
    """`MissingAuthConfig` from the real `require_http_spend_config`, raised before
    `agent.server.main` is ever reached - needs no monkeypatch, same style as the
    missing-[mcp]-config test below."""
    write_mcp_config(project)
    result = runner.invoke(
        cli.app,
        ["mcp", "--project", str(project), "--transport", "http", "--write", "--spend"],
    )

    assert result.exit_code == 2
    assert "[mcp]" in result.stderr
    for key in ("spend_scopes", "spend_rate_limit_per_hour"):
        assert key in result.stderr
    assert "serving" not in result.stdout, "must not claim to serve before validation passes"


def test_http_transport_with_full_spend_config_allows_write_and_spend_together(
    project, monkeypatch
):
    (project / "okragged.toml").write_text(
        MCP_TOML
        + 'spend_scopes = ["okragged:spend"]\n'
        + "spend_rate_limit_per_hour = 5\n",
        encoding="utf-8",
        newline="\n",
    )
    result, seen = served(
        project,
        monkeypatch,
        ["mcp", "--project", str(project), "--transport", "http", "--write", "--spend"],
    )

    assert result.exit_code == 0, result.output
    _, modes = seen[0]
    assert modes["write"] is True
    assert modes["spend"] is True


# --- the missing-config path, exercised for real -----------------------------------


def test_http_transport_with_no_mcp_config_fails_fast_naming_the_missing_keys(project):
    """`MissingAuthConfig` comes from the real `agent.server.main`, raised before
    it builds the MCP server - so this needs no extra and no monkeypatch."""
    result = runner.invoke(cli.app, ["mcp", "--project", str(project), "--transport", "http"])

    assert result.exit_code == 2
    for key in ("issuer_url", "resource_server_url", "jwks_url"):
        assert key in result.stderr
    assert "[mcp]" in result.stderr
    assert "serving" not in result.stdout, "must not claim to serve before validation passes"


# --- the happy paths, main monkeypatched -------------------------------------------


def test_http_transport_with_full_mcp_config_calls_main_with_the_right_arguments(
    project, monkeypatch
):
    write_mcp_config(project)
    result, seen = served(
        project,
        monkeypatch,
        ["mcp", "--project", str(project), "--transport", "http", "--host", "0.0.0.0", "--port", "9000"],
    )

    assert result.exit_code == 0, result.output
    root, modes = seen[0]
    assert root == project.resolve()
    assert modes == {
        "write": False,
        "spend": False,
        "transport": "http",
        "host": "0.0.0.0",
        "port": 9000,
    }
    assert "serving http://0.0.0.0:9000/mcp" in result.stdout


def test_the_default_transport_is_still_stdio_with_no_behaviour_change(project, monkeypatch):
    result, seen = served(project, monkeypatch, ["mcp", "--project", str(project)])

    assert result.exit_code == 0, result.output
    _, modes = seen[0]
    assert modes == {
        "write": False,
        "spend": False,
        "transport": "stdio",
        "host": "127.0.0.1",
        "port": 8001,
    }
    assert result.stdout == "", "stdio is the wire; the http-only startup message must not print"
