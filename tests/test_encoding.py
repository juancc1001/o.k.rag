"""Enforces the I/O encoding policy so the class of bug cannot come back.

Four separate encoding bugs shipped during M1, all the same defect: trusting a
platform default at an I/O boundary. Windows defaults to cp1252, not UTF-8.

The policy, absolute so there is nothing to judge per call site:

  reads   -> encoding="utf-8-sig"          tolerates a BOM, identical without one
  writes  -> encoding="utf-8", newline="\\n"   byte-stable across platforms, no BOM
  procs   -> encoding="utf-8"              never inherit the locale codepage
  stdout  -> reconfigured to UTF-8         the default is the codepage

These are static checks. They are cheap, and they fail the build the moment
someone writes `path.read_text()` with no encoding.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "ragged"

READ_CALLS = {"read_text"}
WRITE_CALLS = {"write_text", "open", "fdopen"}
PROC_CALLS = {"run", "Popen", "check_output"}


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def _literal(node: ast.expr | None) -> object:
    try:
        return ast.literal_eval(node) if node is not None else None
    except ValueError:
        return None


def _name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_write_mode(call: ast.Call) -> bool:
    """open()/fdopen() with a write mode. Positional or keyword."""
    mode = _literal(_kwargs(call).get("mode"))
    if mode is None and len(call.args) >= 2:
        mode = _literal(call.args[1])
    return isinstance(mode, str) and any(c in mode for c in "wax")


def _calls():
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                yield path, node, _name(node), _kwargs(node)


def _where(path: Path, node: ast.Call) -> str:
    return f"{path.relative_to(SRC.parent.parent)}:{node.lineno}"


def test_every_text_read_tolerates_a_bom():
    offenders = [
        f"{_where(p, n)} -> encoding={_literal(kw.get('encoding'))!r}"
        for p, n, name, kw in _calls()
        if name in READ_CALLS and _literal(kw.get("encoding")) != "utf-8-sig"
    ]
    assert not offenders, "reads must use encoding='utf-8-sig':\n  " + "\n  ".join(offenders)


def test_every_text_write_is_utf8_with_lf():
    offenders = []
    for path, node, name, kw in _calls():
        if name not in WRITE_CALLS:
            continue
        if name in {"open", "fdopen"} and not _is_write_mode(node):
            continue  # reads via open() are covered by the mode check below
        if _literal(kw.get("encoding")) != "utf-8":
            offenders.append(f"{_where(path, node)} -> encoding={_literal(kw.get('encoding'))!r}")
        elif _literal(kw.get("newline")) != "\n":
            offenders.append(f"{_where(path, node)} -> newline={_literal(kw.get('newline'))!r}")
    assert not offenders, (
        "writes must use encoding='utf-8', newline='\\n':\n  " + "\n  ".join(offenders)
    )


def test_no_text_read_through_open_without_an_encoding():
    offenders = [
        _where(p, n)
        for p, n, name, kw in _calls()
        if name in {"open", "fdopen"} and not _is_write_mode(n) and "b" not in str(
            _literal(kw.get("mode")) or (_literal(n.args[1]) if len(n.args) >= 2 else "r")
        ) and _literal(kw.get("encoding")) is None
    ]
    assert not offenders, "text reads via open() need an explicit encoding:\n  " + "\n  ".join(
        offenders
    )


def test_subprocess_never_inherits_the_locale_codepage():
    """text=True decodes with the locale codepage and crashes the reader thread."""
    offenders = []
    for path, node, name, kw in _calls():
        if name not in PROC_CALLS:
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "subprocess"):
            continue
        if _literal(kw.get("encoding")) != "utf-8":
            offenders.append(f"{_where(path, node)} -> encoding={_literal(kw.get('encoding'))!r}")
    assert not offenders, (
        "subprocess calls must pass encoding='utf-8':\n  " + "\n  ".join(offenders)
    )


def test_the_policy_test_actually_finds_offenders(tmp_path, monkeypatch):
    """A guard that always passes guards nothing. Point it at a bad file."""
    bad = tmp_path / "ragged"
    bad.mkdir()
    (bad / "bad.py").write_text(
        "from pathlib import Path\nPath('x').read_text()\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "SRC", bad)
    with pytest.raises(AssertionError, match="utf-8-sig"):
        test_every_text_read_tolerates_a_bom()


# --- functional: the failure mode the static checks exist to prevent ------------


def test_cli_output_survives_a_cp1252_console(tmp_path):
    """Redirected stdout defaults to the locale codepage.

    `≥` is not in cp1252, so without the reconfigure this exits non-zero with
    UnicodeEncodeError. Accented Latin text *is* in cp1252, which is why the
    bug looks intermittent.
    """
    (tmp_path / "sources").mkdir()
    (tmp_path / "ragged.toml").write_text('[synth]\nprovider = "gemini"\n', encoding="utf-8")
    doc = tmp_path / "sources" / "simbolos.md"
    doc.write_text(
        "# Política\n\nMárgenes ≥ 20 % — según el análisis (≈3 años).\n",
        encoding="utf-8",
        newline="\n",
    )

    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.run(
        [sys.executable, "-m", "ragged.cli", "extract", str(doc)],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert "UnicodeEncodeError" not in (proc.stderr or ""), proc.stderr
    assert proc.returncode == 0, proc.stderr
    assert "Política" in proc.stdout


def test_a_bom_in_ragged_toml_does_not_break_config_loading(tmp_path):
    from ragged import config as config_module

    (tmp_path / "ragged.toml").write_text(
        '[synth]\nprovider = "gemini"\n', encoding="utf-8-sig"
    )
    cfg = config_module.load(tmp_path)
    assert cfg.synth.provider == "gemini"


def test_a_bom_in_a_concept_does_not_read_as_missing_frontmatter(tmp_path):
    from ragged import okf

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.md").write_text(
        "---\ntype: Document\ntitle: A\ndescription: d\n---\n\nBody.\n",
        encoding="utf-8-sig",
    )
    report = okf.validate(bundle, use_okflint=False)
    assert not report.errors, [str(f) for f in report.errors]
    assert report.concepts == 1


def test_a_bom_in_log_md_does_not_survive_a_rewrite(tmp_path):
    from ragged import okf

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "log.md").write_text("# Update Log\n", encoding="utf-8-sig")
    okf.append_log(bundle, "2026-08-04", ["**Creation**: x."])
    assert not (bundle / "log.md").read_bytes().startswith(b"\xef\xbb\xbf")


def test_written_concepts_use_lf_only(tmp_path):
    from ragged import okf

    bundle = tmp_path / "bundle"
    okf.write_concept(
        bundle,
        okf.Concept(concept_id="a", type="Document", title="A", description="d", body="x\ny"),
    )
    okf.write_root_index(bundle, "0.2", "T", [("a", "d")])
    okf.append_log(bundle, "2026-08-04", ["**Creation**: x."])
    okf.write_manifest(tmp_path, "bundle", "T")

    for path in (
        bundle / "a.md",
        bundle / "index.md",
        bundle / "log.md",
        tmp_path / okf.MANIFEST_NAME,
    ):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF"
