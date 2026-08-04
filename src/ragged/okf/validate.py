"""Conformance checks.

Three rules make a bundle conformant (unchanged v0.1 -> v0.2):
  1. every non-reserved .md has parseable YAML frontmatter
  2. every frontmatter has a non-empty `type`
  3. reserved files (index.md, log.md) follow their defined structure

Everything else is a warning. Consumers MUST NOT reject a bundle for missing
optional fields, unknown types, unknown keys, broken links, or missing indexes.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .concept import parse, split
from .manifest import MANIFEST_NAME

RESERVED = {"index.md", "log.md"}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+?)\)")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Finding:
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} {self.path}: {self.message}"


@dataclass
class Report:
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    concepts: int = 0
    okflint_exit: int | None = None
    okflint_output: str = ""
    okflint_note: str = ""
    """Why okflint did not run, when it didn't."""

    @property
    def ok(self) -> bool:
        return not self.errors and self.okflint_exit in (None, 0)


def validate(bundle_root: Path, use_okflint: bool = True) -> Report:
    report = Report()
    md_files = sorted(p for p in bundle_root.rglob("*.md"))
    concept_ids = {
        p.relative_to(bundle_root).with_suffix("").as_posix()
        for p in md_files
        if p.name not in RESERVED
    }

    for path in md_files:
        rel = path.relative_to(bundle_root).as_posix()
        # utf-8-sig: concepts may have been hand-edited, and a BOM before the
        # opening `---` would otherwise read as missing frontmatter (E1)
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if path.name == "index.md":
            _check_index(report, rel, text, is_bundle_root=rel == "index.md")
        elif path.name == "log.md":
            _check_log(report, rel, text)
        else:
            _check_concept(report, rel, text, concept_ids)

    for directory in sorted({p.parent for p in md_files}):
        if not (directory / "index.md").exists():
            rel = directory.relative_to(bundle_root).as_posix() or "."
            report.warnings.append(Finding("W4", rel, "no index.md in directory"))

    if use_okflint:
        _run_okflint(report, bundle_root)

    return report


def _check_concept(report: Report, rel: str, text: str, concept_ids: set[str]) -> None:
    try:
        concept = parse(text, rel)
    except ValueError as exc:
        code = "E2" if "type" in str(exc) else "E1"
        report.errors.append(Finding(code, rel, str(exc).split(": ", 1)[-1]))
        return

    report.concepts += 1
    for name in ("title", "description"):
        if not getattr(concept, name):
            report.warnings.append(Finding("W1", rel, f"missing recommended `{name}`"))
    if not (concept.generated or {}).get("by"):
        report.warnings.append(Finding("W3", rel, "no `generated.by`"))
    for target in _broken_links(concept.body, rel, concept_ids):
        report.warnings.append(Finding("W2", rel, f"broken cross-link `{target}`"))


def _check_index(report: Report, rel: str, text: str, is_bundle_root: bool) -> None:
    fm, _ = split(text)
    if fm is None:
        return
    if not is_bundle_root:
        report.errors.append(
            Finding("E3", rel, "only the bundle-root index.md may have frontmatter")
        )


def _check_log(report: Report, rel: str, text: str) -> None:
    fm, _ = split(text)
    if fm is not None:
        report.errors.append(Finding("E3", rel, "log.md must not have frontmatter"))
    dates = [
        line[3:].strip()
        for line in text.splitlines()
        if line.startswith("## ")
    ]
    for date in dates:
        if not ISO_DATE_RE.match(date):
            report.warnings.append(Finding("W5", rel, f"date heading not ISO 8601: `{date}`"))
    parsed = [d for d in dates if ISO_DATE_RE.match(d)]
    if parsed != sorted(parsed, reverse=True):
        report.warnings.append(Finding("W5", rel, "date headings not newest-first"))


def _broken_links(body: str, rel: str, concept_ids: set[str]) -> list[str]:
    broken = []
    here = Path(rel).parent
    for target in LINK_RE.findall(body):
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        clean = target.split("#", 1)[0].split("?", 1)[0]
        if not clean.endswith(".md"):
            continue
        if clean.startswith("/"):
            resolved = clean.lstrip("/")
        else:
            resolved = (here / clean).as_posix()
        resolved = Path(resolved).as_posix().removesuffix(".md")
        # normalise any ../ segments
        parts: list[str] = []
        for seg in resolved.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg not in ("", "."):
                parts.append(seg)
        if "/".join(parts) not in concept_ids:
            broken.append(target)
    return broken


def _run_okflint(report: Report, bundle_root: Path) -> None:
    """okflint is the normative gate; the checks above are the fallback floor.

    `validate` always resolves a manifest (default okf-base.yaml) and exits 2
    without one, so skip rather than invoke it and report a bad-manifest failure.
    """
    exe = shutil.which("okflint")
    if exe is None:
        report.okflint_note = "okflint not installed (uv tool install okflint)"
        return
    manifest = bundle_root.parent / MANIFEST_NAME
    if not manifest.exists():
        report.okflint_note = f"no {MANIFEST_NAME}; run `ragged build` to generate one"
        return
    proc = subprocess.run(
        [exe, "validate", "--manifest", str(manifest), str(bundle_root)],
        capture_output=True,
        text=True,
        # never inherit the Windows locale codepage — okflint emits non-cp1252 bytes
        encoding="utf-8",
        errors="replace",
        # `encoding` above only governs how *we* decode. okflint's own stdout is a
        # pipe, so its Python picks the locale codepage and crashes printing the
        # `→` in its own findings. Tell the child to encode UTF-8 too.
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    report.okflint_exit = proc.returncode
    report.okflint_output = f"{proc.stdout or ''}{proc.stderr or ''}".strip()
