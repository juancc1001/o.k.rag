"""Enforces the I/O encoding policy so the class of bug cannot come back.

Four separate encoding bugs shipped during M1, all the same defect: trusting a
platform default at an I/O boundary. Windows defaults to cp1252, not UTF-8.

The policy, absolute so there is nothing to judge per call site:

  reads   -> encoding="utf-8-sig"          tolerates a BOM, identical without one
  writes  -> encoding="utf-8", newline="\\n"   byte-stable across platforms, no BOM
  procs   -> encoding="utf-8"              never inherit the locale codepage
  procs   -> PYTHONIOENCODING=utf-8 in env the child's stdout is a pipe, so its
                                           Python picks the codepage on its own
  stdout  -> reconfigured to UTF-8         the default is the codepage

These are static checks. They are cheap, and they fail the build the moment
someone writes `path.read_text()` with no encoding.
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "okragged"

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


def _subprocess_calls():
    for path, node, name, kw in _calls():
        if name not in PROC_CALLS:
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "subprocess":
            yield path, node, kw


def test_subprocess_tells_the_child_to_encode_utf8_too():
    """`encoding=` governs our decode only. The child's stdout is a pipe, so its
    Python falls back to the locale codepage and crashes on its own output."""
    offenders = []
    for path, node, kw in _subprocess_calls():
        env = kw.get("env")
        source = ast.unparse(env) if env is not None else "<no env>"
        if "PYTHONIOENCODING" not in source:
            offenders.append(f"{_where(path, node)} -> env={source}")
    assert not offenders, (
        "subprocess calls must pass PYTHONIOENCODING=utf-8 in env:\n  " + "\n  ".join(offenders)
    )


def test_the_policy_test_actually_finds_offenders(tmp_path, monkeypatch):
    """A guard that always passes guards nothing. Point it at a bad file."""
    bad = tmp_path / "okragged"
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
    (tmp_path / "okragged.toml").write_text('[synth]\nprovider = "gemini"\n', encoding="utf-8")
    doc = tmp_path / "sources" / "simbolos.md"
    doc.write_text(
        "# Política\n\nMárgenes ≥ 20 % — según el análisis (≈3 años).\n",
        encoding="utf-8",
        newline="\n",
    )

    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.run(
        [sys.executable, "-m", "okragged.cli", "extract", str(doc)],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert "UnicodeEncodeError" not in (proc.stderr or ""), proc.stderr
    assert proc.returncode == 0, proc.stderr
    assert "Política" in proc.stdout


def test_a_piped_child_needs_the_env_to_print_non_cp1252_output():
    """The okflint crash, minimally: it prints `→` in every finding it reports.

    Its stdout is a pipe, so `encoding='utf-8'` on our side does nothing for it —
    the child's own Python picks the locale codepage and dies inside print().
    """
    code = "print('→ [E1] a.md')"
    cp1252 = {**os.environ, "PYTHONIOENCODING": "cp1252"}

    crashed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=cp1252,
    )
    assert "UnicodeEncodeError" in (crashed.stderr or ""), crashed.stderr

    fixed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**cp1252, "PYTHONIOENCODING": "utf-8"},
    )
    assert fixed.returncode == 0, fixed.stderr
    assert "→" in fixed.stdout


def test_okflint_is_invoked_with_a_utf8_child_encoding(tmp_path, monkeypatch):
    # okf/__init__ re-exports the `validate` function, shadowing the module name
    validate_module = importlib.import_module("okragged.okf.validate")

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (tmp_path / validate_module.MANIFEST_NAME).write_text(
        "version: 1\n", encoding="utf-8", newline="\n"
    )

    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(validate_module.shutil, "which", lambda _: "okflint")
    monkeypatch.setattr(validate_module.subprocess, "run", fake_run)
    validate_module.validate(bundle)

    assert seen["encoding"] == "utf-8"  # our decode
    assert seen["env"]["PYTHONIOENCODING"] == "utf-8"  # the child's encode
    assert "PATH" in seen["env"], "env must extend os.environ, not replace it"


def test_a_bom_in_okragged_toml_does_not_break_config_loading(tmp_path):
    from okragged import config as config_module

    (tmp_path / "okragged.toml").write_text(
        '[synth]\nprovider = "gemini"\n', encoding="utf-8-sig"
    )
    cfg = config_module.load(tmp_path)
    assert cfg.synth.provider == "gemini"


def test_a_bom_in_a_concept_does_not_read_as_missing_frontmatter(tmp_path):
    from okragged import okf

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
    from okragged import okf

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "log.md").write_text("# Update Log\n", encoding="utf-8-sig")
    okf.append_log(bundle, "2026-08-04", ["**Creation**: x."])
    assert not (bundle / "log.md").read_bytes().startswith(b"\xef\xbb\xbf")


def test_written_concepts_use_lf_only(tmp_path):
    from okragged import okf

    bundle = tmp_path / "bundle"
    okf.write_concept(
        bundle,
        okf.Concept(concept_id="a", type="Document", title="A", description="d", body="x\ny"),
    )
    okf.write_indexes(bundle, "0.2", "T")
    okf.append_log(bundle, "2026-08-04", ["**Creation**: x."])
    okf.write_manifest(tmp_path, "bundle", "T")

    for path in (
        bundle / "a.md",
        bundle / "index.md",
        bundle / "log.md",
        tmp_path / okf.MANIFEST_NAME,
    ):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF"
