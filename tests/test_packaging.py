"""Guards the defect class fixed in `4cac126`: a file present on disk, silently
absent from a built artifact. That commit anchored the `sources/` gitignore
rule (unanchored, it also matched `src/okrag/sources/`) and force-included
the gitignored `api/static/**` view assets via `[tool.hatch.build.targets.*]
artifacts`. Static, not trusted-to-hold, the same way `tests/test_encoding.py`
guards its own defect class by re-deriving the check rather than by relying on
the fix.

Builds the sdist and wheel for real, from this checkout, via `python -m
build --no-isolation` - offline and no PyPI access, since both the build
frontend (`build`) and the build backend `[build-system]` declares
(`hatchling`) are dev dependencies, so a normal `pip install -e ".[dev]"`
already has them importable in the running interpreter - exactly as
`okf/validate.py` invokes a real `okflint` subprocess rather than mocking one.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _importable(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


pytestmark = pytest.mark.skipif(
    not (_importable("hatchling") and _importable("build")),
    reason='the "dev" extra was not installed: pip install -e ".[dev]"',
)

_view_built = pytest.mark.skipif(
    not (REPO_ROOT / "src/okrag/api/static/index.html").is_file(),
    reason="web/ has never been built in this checkout: run `npm install && npm run build` "
    "in web/, or `okrag build-view`",
)


@pytest.fixture(scope="module")
def built_artifacts(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("dist")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(out_dir), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode == 0, f"python -m build failed:\n{proc.stdout}\n{proc.stderr}"

    wheels = list(out_dir.glob("*.whl"))
    sdists = list(out_dir.glob("*.tar.gz"))
    assert len(wheels) == 1, wheels
    assert len(sdists) == 1, sdists
    return {"wheel": wheels[0], "sdist": sdists[0]}


def test_the_wheel_carries_the_sources_module(built_artifacts):
    with zipfile.ZipFile(built_artifacts["wheel"]) as z:
        names = z.namelist()
    assert "okrag/sources/__init__.py" in names
    assert "okrag/sources/local.py" in names


@_view_built
def test_the_wheel_carries_the_built_graph_view(built_artifacts):
    with zipfile.ZipFile(built_artifacts["wheel"]) as z:
        names = z.namelist()
    assert "okrag/api/static/index.html" in names


def test_the_sdist_carries_the_sources_module(built_artifacts):
    with tarfile.open(built_artifacts["sdist"]) as t:
        names = t.getnames()
    assert any(n.endswith("src/okrag/sources/__init__.py") for n in names)
    assert any(n.endswith("src/okrag/sources/local.py") for n in names)


@_view_built
def test_the_sdist_carries_the_built_graph_view(built_artifacts):
    with tarfile.open(built_artifacts["sdist"]) as t:
        names = t.getnames()
    assert any(n.endswith("src/okrag/api/static/index.html") for n in names)
