"""`okrag build-view` / `okrag serve --all`: staleness detection and the
npm subprocess wrapper, kept offline by monkeypatching subprocess itself - a
cold `node_modules` needs network, which the suite's no-network invariant forbids.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from okrag import cli

runner = CliRunner()


def _touch(path: Path, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))


def _web_layout(tmp_path: Path) -> tuple[Path, Path]:
    web_root = tmp_path / "web"
    static_dir = tmp_path / "static"
    _touch(web_root / "index.html")
    _touch(web_root / "package.json")
    _touch(web_root / "vite.config.ts")
    _touch(web_root / "src" / "main.tsx")
    return web_root, static_dir


def test_view_is_stale_when_the_build_has_never_run(tmp_path):
    web_root, static_dir = _web_layout(tmp_path)
    assert cli._view_is_stale(web_root, static_dir) is True


def test_view_is_not_stale_when_the_build_is_newer_than_every_source_file(tmp_path):
    web_root, static_dir = _web_layout(tmp_path)
    now = time.time()
    for p in (web_root / "index.html", web_root / "package.json", web_root / "vite.config.ts", web_root / "src" / "main.tsx"):
        import os

        os.utime(p, (now - 10, now - 10))
    _touch(static_dir / "index.html", mtime=now)
    assert cli._view_is_stale(web_root, static_dir) is False


def test_view_is_stale_when_a_source_file_changed_after_the_build(tmp_path):
    web_root, static_dir = _web_layout(tmp_path)
    now = time.time()
    _touch(static_dir / "index.html", mtime=now - 10)
    import os

    os.utime(web_root / "src" / "main.tsx", (now, now))
    assert cli._view_is_stale(web_root, static_dir) is True


def test_build_view_reports_missing_npm_without_touching_subprocess(tmp_path, monkeypatch):
    web_root, _ = _web_layout(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)

    def _fail(*a, **k):
        raise AssertionError("subprocess.run must not be called when npm is missing")

    monkeypatch.setattr(cli.subprocess, "run", _fail)

    error = cli._build_view(web_root)
    assert error is not None
    assert "npm" in error


def test_build_view_skips_install_when_node_modules_is_present(tmp_path, monkeypatch):
    web_root, _ = _web_layout(tmp_path)
    (web_root / "node_modules").mkdir()
    monkeypatch.setattr(cli.shutil, "which", lambda _: "npm")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    error = cli._build_view(web_root)
    assert error is None
    assert len(calls) == 1
    assert calls[0][1:] == ["run", "build"]


def test_build_view_installs_then_builds_when_node_modules_is_missing(tmp_path, monkeypatch):
    web_root, _ = _web_layout(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "npm")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    error = cli._build_view(web_root)
    assert error is None
    assert [c[1:] for c in calls] == [["install"], ["run", "build"]]


def test_build_view_reports_the_child_failure(tmp_path, monkeypatch):
    web_root, _ = _web_layout(tmp_path)
    (web_root / "node_modules").mkdir()
    monkeypatch.setattr(cli.shutil, "which", lambda _: "npm")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "vite: command not found")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    error = cli._build_view(web_root)
    assert error is not None
    assert "vite: command not found" in error


def test_build_view_command_reports_a_missing_web_root_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_web_root", lambda: tmp_path / "no-web-here")

    def _fail(*a, **k):
        raise AssertionError("subprocess.run must not be called when web/ does not exist")

    monkeypatch.setattr(cli.subprocess, "run", _fail)

    result = runner.invoke(cli.app, ["build-view"])
    assert result.exit_code == 0, result.output
    assert "installed wheel" in result.output
