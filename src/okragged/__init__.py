"""okragged as a library — the operations `okragged` the command runs, callable.

Thin, and deliberately so. Every module here has always been importable, and these
functions are the ones the CLI itself calls; what this module adds is a **curated
surface** — one place to import from, a first argument that may be a project
directory instead of a `Config`, and a contract that does not move when the
internal layout does. Each function resolves that argument and delegates. Nothing
here reimplements a stage, and nothing wraps a return value: what comes back is
what the underlying function returns, and a bundle is a directory on disk (§2),
which is why no object stands in for one.

    import okragged

    cfg = okragged.configure("~/corpus")
    okragged.build(cfg, progress=print)
    okragged.check(cfg)

**No `.env` is read from this path.** `cli.py` and `agent/server.py` call
`load_dotenv` because they are entry points for humans; credential discovery is
the caller's business, so put the provider key in the environment (or pass a
`Config` for a project whose keys you loaded yourself) before calling `build`,
`index` or `add_source`.

**`build()` does not write layer 3; `okragged build` does.** The asymmetry is
deliberate and the reason is in `pipeline.ingest`. `write_contract()` is here for
a caller who wants `agent/AGENTS.md` and `.mcp.json` written.

Imports are deferred — function-local, plus a module `__getattr__` (PEP 562) for
the two names — so `import okragged` stays silent and cheap. The extraction path
pulls in markitdown, which prints an ffmpeg warning on import, and a library that
writes to somebody else's stderr in order to be imported is broken.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # annotations only; importing these eagerly is the thing to avoid
    from collections.abc import Callable

    from .agent.lint import LintReport
    from .agent.query import SearchResult
    from .agent.write import AddedSource
    from .config import Config
    from .okf import Report
    from .pipeline import IngestSummary, Progress
    from .vectors import IndexSummary

__version__ = "0.1.0"

__all__ = [
    "Config",
    "Progress",
    "__version__",
    "add_source",
    "build",
    "check",
    "configure",
    "index",
    "lint",
    "read_concept",
    "search",
    "write_contract",
]


def configure(project: str | Path, **overrides: Any) -> Config:
    """The `Config` for `project`, reading its `okragged.toml` if it has one.

    Keyword arguments override whole sections and are validated by pydantic:
    `configure(path, synth={"provider": "gemini"})`. Reads no `.env`.
    """
    from .config import Config, load

    cfg = load(Path(project).expanduser())
    if not overrides:
        return cfg
    return Config.model_validate({**cfg.model_dump(), **overrides})


def build(
    project: str | Path | Config,
    *,
    dry_run: bool = False,
    only: list[str] | None = None,
    contract: bool = False,
    progress: Callable[[Progress], None] | None = None,
) -> IngestSummary:
    """Run the ingest pipeline: discover → extract → plan → synthesize → link-weave
    → emit → validate. Needs a credential for the configured `[synth]` provider,
    unless `dry_run`.

    `contract=False` is the one place this differs from `okragged build`, which
    always regenerates layer 3; see `pipeline.ingest`. `only` limits the run to
    the given source paths, which also suppresses the vanished-source sweep (§6.1).
    """
    from .pipeline import ingest

    return ingest(
        _config(project), dry_run=dry_run, only=only, contract=contract, progress=progress
    )


def index(project: str | Path | Config) -> IndexSummary:
    """Embed the bundle, project it to 2D, write `graph.json`. Writes only into
    `.okragged/`, and needs a credential for the `[vectors]` provider."""
    from .vectors import index as index_bundle

    return index_bundle(_config(project))


def check(project: str | Path | Config) -> Report:
    """Validate an existing bundle without rebuilding it. Offline."""
    from . import okf

    return okf.validate(_config(project).bundle_root)


def search(project: str | Path | Config, query: str, k: int = 8) -> SearchResult:
    """Rank concepts against `query` and return pointers, never bodies (§10).
    Falls back to index traversal without stored vectors or a credential."""
    from .agent import search as search_bundle

    return search_bundle(_config(project), query, k)


def read_concept(project: str | Path | Config, concept_id: str) -> str:
    """One concept as it is on disk, frontmatter included. Offline."""
    from .agent import read_concept as read

    return read(_config(project), concept_id)


def lint(project: str | Path | Config) -> LintReport:
    """Conformance plus what okflint cannot see: ghosts, stale concepts, orphans.
    Offline."""
    from .agent import lint as lint_bundle

    return lint_bundle(_config(project))


def add_source(
    project: str | Path | Config, text: str, title: str | None = None
) -> AddedSource:
    """Record prose in `sources/` and ingest that one path (§13 M5). The path is
    generated from the text, never supplied. Needs a `[synth]` credential."""
    from .agent.write import add_source as add

    return add(_config(project), text, title=title)


def write_contract(project: str | Path | Config) -> list[Path]:
    """Write layer 3 — `agent/AGENTS.md` and the `.mcp.json` entry — beside the
    bundle. Explicit here because `build()` does not do it. Offline."""
    from .agent import write_contract as write

    return write(_config(project))


def _config(project: str | Path | Config) -> Config:
    from .config import Config, load

    if isinstance(project, Config):
        return project
    return load(Path(project).expanduser())


def __getattr__(name: str) -> Any:
    """`Config` and `Progress` on demand: naming them at import time would pull in
    pydantic and the whole pipeline for a caller who only wanted `__version__`."""
    if name == "Config":
        from .config import Config

        return Config
    if name == "Progress":
        from .pipeline import Progress

        return Progress
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """The curated surface, and only it (PEP 562).

    `__getattr__` alone leaves `Config` and `Progress` out of `dir()` and out of
    every completion that reads it, so two of the twelve public names would be
    undiscoverable — while `Path`, `Any`, `TYPE_CHECKING` and `annotations` leak
    in from this module's own imports. Answering from `__all__` fixes both, and
    keeps the one list that has to be right.
    """
    return sorted(__all__)
