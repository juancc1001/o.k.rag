"""okragged as a library: the operations `okragged` the command runs, callable directly.

    import okragged

    cfg = okragged.configure("~/corpus")
    okragged.build(cfg, progress=print)
    okragged.check(cfg)

Each function takes a project path or a `Config` and delegates to the module that
does the real work. A bundle is just a directory on disk (DESIGN.md §2), so no
object stands in for one here.

No `.env` is read from this module — pass a `Config` you built with your own
credentials, or set the provider key in the environment yourself. (`cli.py` and
`agent/server.py` load `.env` themselves, since they're the human-facing entry
points.)

`build()` regenerates `agent/AGENTS.md` (docs describing the bundle, for an AI
agent to read) but never touches `.mcp.json` (the machine's MCP client config) —
only `register()` does that, and nothing here calls it automatically.

Every import below is deferred (function-local, plus `__getattr__` for `Config`
and `Progress`), so `import okragged` stays cheap: the extraction path pulls in
`markitdown`, which prints a warning on import.
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

def _installed_version() -> str:
    """Reads the version from package metadata, so `pyproject.toml` stays the
    only place it's written — a hand-kept copy could drift from `[bundle].actor`,
    which stamps this into every concept's `generated.by`.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("okragged")
    except PackageNotFoundError:  # pragma: no cover - an uninstalled checkout
        return "0+unknown"

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
    "register",
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
    contract: bool = True,
    progress: Callable[[Progress], None] | None = None,
) -> IngestSummary:
    """Runs the ingest pipeline: discover -> extract -> plan -> synthesize ->
    link-weave -> emit -> validate. Needs a credential for the configured
    `[synth]` provider, unless `dry_run`.

    Regenerates `agent/AGENTS.md` (docs describing the bundle) by default, same
    as `okragged build` — pass `contract=False` to skip it. Never registers the
    MCP server; that's `register()`.

    `only` limits the run to the given source paths, and also skips the sweep
    that marks vanished sources deprecated (DESIGN.md §6.1).
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
    """Ranks concepts against `query` and returns pointers, never bodies — the
    caller fetches only what it needs (DESIGN.md §10). Falls back to graph
    traversal when there's no stored embedding or credential."""
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
    project: str | Path | Config,
    text: str,
    title: str | None = None,
    synthesize: bool = False,
) -> AddedSource:
    """Records prose in `sources/` as a new source. Offline by default.

    The path is generated from the text, never supplied. Recording alone leaves
    the concept for the next `build()` to author; `synthesize` authors it here
    instead — one model call, and it reads no `.env` (see the module docstring).
    """
    from .agent.write import add_source as add

    return add(_config(project), text, title=title, synthesize=synthesize)


def write_contract(project: str | Path | Config) -> list[Path]:
    """Regenerate `agent/AGENTS.md` beside the bundle. Offline.

    `build()` already does this; call it directly to refresh the contract without
    rebuilding. It does not register the server — see `register()`.
    """
    from .agent import write_contract as write

    return write(_config(project))


def register(
    project: str | Path | Config, *, write: bool = False, spend: bool = False
) -> Path:
    """Registers `okragged mcp` for this project in `.mcp.json`, preserving any
    other servers already registered there. Offline.

    The only function here that writes a file okragged doesn't own — nothing
    calls it automatically, which is why it's a separate function rather than
    a flag on `build()`.

    `write` and `spend` become arguments in the registered command, so the
    server it spawns offers `add_source` and may use your credential. Leave
    both off and the entry stays bare — that server is read-only.
    """
    from .agent import register as do_register

    return do_register(_config(project), write=write, spend=spend)


def _config(project: str | Path | Config) -> Config:
    from .config import Config, load

    if isinstance(project, Config):
        return project
    return load(Path(project).expanduser())


def __getattr__(name: str) -> Any:
    """`Config`, `Progress` and `__version__` on demand: naming them at import time
    would pull in pydantic, the whole pipeline, and a metadata read off disk for a
    caller who only wanted one function."""
    if name == "__version__":
        return _installed_version()
    if name == "Config":
        from .config import Config

        return Config
    if name == "Progress":
        from .pipeline import Progress

        return Progress
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Returns exactly `__all__` (PEP 562) — without this, `Config` and
    `Progress` are invisible to `dir()`/autocomplete since `__getattr__` alone
    doesn't register them, while this module's own imports (`Path`, `Any`, ...)
    would otherwise leak in instead.
    """
    return sorted(__all__)
