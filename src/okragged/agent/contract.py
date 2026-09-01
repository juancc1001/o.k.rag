"""Generates the agent contract: `AGENTS.md` and the `.mcp.json` registration
entry (DESIGN.md §2, §10 — "the agent contract" is how an AI agent discovers
and reads a bundle without knowing okragged exists).

Two artifacts, written by two separate functions because they have different
natures:

`agent/AGENTS.md` is a *function of the bundle* — concept count, structure
tree, type vocabulary. It changes whenever the bundle changes, so
`write_contract` runs after every commit: it must never describe a bundle
that no longer exists. It carries no timestamp, so an unchanged bundle
regenerates to the same bytes.

`.mcp.json` is not a function of the bundle at all — the entry is the same
bytes whether the bundle holds zero concepts or a thousand. So `register` is
a **deliberate act** (`okragged agent --register`), never a side effect of
building.

What the `.mcp.json` entry *is* a function of is the machine, and only where
it has to be: it names the bare `okragged` command when `PATH` can resolve
it, and falls back to the console script beside this interpreter when it
can't — the same reasoning as `okf/validate.py::_okflint`. A client spawns
the server from `.mcp.json` rather than from an activated shell, so a bare
name that only an activated venv can resolve gets the server killed at spawn
with nothing to read anywhere. `PATH` first, not beside-first, because this
file isn't gitignored and may get shared: portable when portability is
available, correct when it isn't.

The distinction matters because `.mcp.json` is the one file here okragged
doesn't own: it's the MCP config for whatever agent works in that directory,
shared with every other server registered there. Merging into somebody
else's config unasked, on every build, would be a write with no justification.
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from .. import okf
from ..config import Config
from ..okf import Concept

AGENTS_NAME = "AGENTS.md"
MCP_CONFIG_NAME = ".mcp.json"
SERVER_KEY = "okragged"
COMMAND = "okragged"
"""The console script — not an interpreter path and not a script path, since
the consumer of a foreign entry is in a different tree entirely. Only
`_command` may substitute an absolute path, and only for the local entry."""

CONTRACT_URI = "okragged://contract"
INDEX_URI = "okragged://index"
"""The two resources the server exports (DESIGN.md §10). Named here, not in
`server.py`, so the contract can quote them without needing the MCP SDK
installed."""


def write_contract(config: Config) -> list[Path]:
    """Regenerate `agent/AGENTS.md`. Does **not** register the server.

    A list of one, because the caller reports what was written and because the
    contract may yet grow a second derived file — registration will not be it.
    """
    return [_write(config.project_root / "agent" / AGENTS_NAME, render_agents_md(config))]


def register(config: Config, *, write: bool = False, spend: bool = False) -> Path:
    """Writes the `.mcp.json` entry, merged into whatever else is registered.

    The one function here that writes a file okragged doesn't own — nothing
    calls it automatically, only `okragged agent --register`.

    `write`/`spend` land in the entry only when asked for in that same command
    (`--register --write --spend`); omitted, the entry is bare and the server
    it spawns stays read-only. That matters because `.mcp.json` is shared —
    an entry that could spend by accident is exactly what these switches
    prevent.
    """
    return _write(
        config.project_root / MCP_CONFIG_NAME,
        render_mcp_json(config, write=write, spend=spend),
    )


def is_registered(config: Config) -> bool:
    """Whether `.mcp.json` already registers this project's server.

    Matches by the server key, not the whole entry — an operator may have
    added `--write`/`--spend` to its `args` by hand, and comparing the full
    entry would misread that as unregistered.

    Only used so a build can *mention* it's unregistered — acting on it would
    be exactly the side effect this module exists to avoid.
    """
    path = config.project_root / MCP_CONFIG_NAME
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(loaded, dict) and SERVER_KEY in (loaded.get("mcpServers") or {})


def render_mcp_json(config: Config, *, write: bool = False, spend: bool = False) -> str:
    """The registration entry, merged into whatever else is already registered."""
    path = config.project_root / MCP_CONFIG_NAME
    existing: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            existing = {}
    servers = dict(existing.get("mcpServers") or {})
    servers[SERVER_KEY] = _entry(config.project_root, local=True, write=write, spend=spend)
    return json.dumps({**existing, "mcpServers": servers}, indent=2) + "\n"


def _entry(
    project_root: Path, *, local: bool, write: bool = False, spend: bool = False
) -> dict[str, object]:
    """Builds the `mcpServers` entry value.

    A client spawns the server with the config's directory as cwd, so the
    local entry (written into this project root) needs no target — cwd
    already answers it. A foreign entry (copied into somebody else's config)
    carries the project root absolutely instead, since nothing about a
    foreign checkout can be said relative to this one.

    **`write`/`spend` reach the local entry only.** The foreign copy (in
    `AGENTS.md`) is for somebody else's machine, and what this operator
    authorized here isn't a fact about theirs — shipping `--spend` in it
    would be telling a stranger to spend a key. The contract explains this in
    prose beside the flagless JSON, so a consumer who wants to write adds it
    knowingly.
    """
    args = ["mcp"]
    if not local:
        return {"command": COMMAND, "args": args + ["--project", project_root.resolve().as_posix()]}
    if write:
        args.append("--write")
    if spend:
        args.append("--spend")
    return {"command": _command(), "args": args}


def _command() -> str:
    """Resolves `okragged` via `PATH` first, then beside this interpreter.

    A client spawns the registered command directly, not from an activated
    shell, so an `okragged` installed only into a venv is invisible to it and
    the server dies at spawn with a bare `CONNECTION_CLOSED`. Falls back to
    beside `sys.executable`, the same reasoning as `okf/validate.py::_okflint`.
    `PATH` is tried first (not beside-first) because `.mcp.json` isn't
    gitignored: a bare resolvable name travels to another checkout, an
    absolute path doesn't.
    """
    if shutil.which(COMMAND):
        return COMMAND
    beside = Path(sys.executable).parent / (f"{COMMAND}.exe" if os.name == "nt" else COMMAND)
    return str(beside) if beside.is_file() else COMMAND


def render_agents_md(config: Config) -> str:
    concepts = okf.read_concepts(config.bundle_root)
    bundle = config.bundle.root
    foreign = {"mcpServers": {SERVER_KEY: _entry(config.project_root, local=False)}}
    lines = [
        f"# {config.bundle_title} — agent contract",
        "",
        f"Generated by okragged from `{bundle}/`. Regenerated on every `okragged build` "
        "and by `okragged agent`, so edits here are overwritten.",
        "",
        f"`{bundle}/` is an **Open Knowledge Format v{config.bundle.okf_version}** bundle: "
        f"markdown concepts with YAML frontmatter, an `index.md` per directory, and a "
        f"`log.md`. It holds **{len(concepts)} concept(s)**. Nothing else is needed to read "
        "it — no SDK, no database, no embeddings.",
        "",
        "## How to query it",
        "",
        # named as a resource before it is named as a path: this text is served as
        # `okragged://contract` to a consumer who has no `bundle/` to open
        f"1. **Read the root index first** — `{INDEX_URI}` over the protocol, or "
        f"`{bundle}/index.md` if you hold the directory. It lists every top-level concept "
        "with its own title and description. An entry that ends "
        "`([n more](./<subdir>/index.md))` owns a subdirectory — follow it down with "
        "`read_concept`.",
        "2. **Then read whole concept files.** A concept is the unit of knowledge here, and "
        "its frontmatter carries the provenance its body depends on. Do not search for "
        "chunks and do not read fragments: read the file.",
        "3. **Follow the cross-links in the prose.** They are bundle-absolute "
        "(`/finance/pricing.md`) and inline, because a link asserts a relationship and the "
        "sentence around it says which. There is no \"Related\" section by design.",
        "",
        "`search` is a way in, not a substitute for the indexes: it returns pointers — "
        "concept id, title, description, score — and never a body. You decide what to read.",
        "",
        "## Structure",
        "",
        "```",
        *_structure(bundle, concepts),
        "```",
        "",
        "## Type vocabulary",
        "",
        "`type` is free-form in OKF; this is the vocabulary this bundle actually uses.",
        "",
        "| type | concepts |",
        "|---|---|",
        *_types(concepts),
        "",
        "## Trust",
        "",
        "| field | what it means |",
        "|---|---|",
        "| `status: draft` | okragged wrote it and nobody has confirmed it. The default. |",
        "| `status: stable` | a human promoted it by editing the file. |",
        "| `status: deprecated` | its source is gone. Kept for links and history. |",
        "| no `verified` block | trust tier **unverified**. |",
        "| `verified` by `human:…` | trust tier **human-reviewed**. |",
        "| `verified` by anything else | trust tier **machine-confirmed**. |",
        "",
        "**A `verified` entry records a confirmation event that actually happened** — "
        "writing one because the content looks correct forges the human-reviewed tier. A "
        "human promotes a concept by editing the file; some hosted instances additionally "
        "offer a `verify_concept` tool, restricted to a configured scope, that appends a "
        "real confirmation attributed to that human's own verified identity rather than to "
        "a string they typed. If your tool list shows it, its own description says so.",
        "",
        "## Operations",
        "",
        "Three tools always, and a fourth — `add_source` — when the instance was started "
        "to write. **What this instance offers is what its tool list shows**, and each "
        "tool's own description says which of the modes below you are talking to. This "
        "file cannot: how a server was started is not a fact about the knowledge it "
        "serves, so what follows describes the modes rather than asserting one.",
        "",
        "| tool | signature |",
        "|---|---|",
        "| `search` | `(query: str, k: int = 8)` → `path`, `note`, and pointers: "
        "`concept_id`, `title`, `description`, `score` |",
        "| `read_concept` | `(concept_id: str)` → the raw file, frontmatter included |",
        "| `lint` | `()` → conformance, ghost edges, stale concepts, orphans |",
        "| `add_source` | `(text: str, title: str \\| None)` → the source it wrote, and "
        "the concepts it authored where the instance authors them |",
        "",
        "| how it was started | what that instance offers |",
        "|---|---|",
        f"| `{COMMAND} mcp` | Read-only, and no credential is read at all: `search` ranks "
        "by traversing the indexes rather than by embedding your query. |",
        f"| `{COMMAND} mcp --write` | Adds `add_source`. **It records only** — the concept "
        "is authored by the next `okragged build`, so do not wait for one or report that "
        "it appeared. |",
        f"| `{COMMAND} mcp --write --spend` | `add_source` authors the concept in the same "
        "call and embeds it, and `search` may embed your query. Both spend the operator's "
        "credential, and nothing here limits the rate. |",
        "",
        "**So whether anything here spends money is a property of the instance rather than "
        "of the protocol, and reading a concept never does.** Where an instance does not "
        "spend, the step that costs money — turning a source into a concept — is "
        "`okragged build`, run by whoever owns the corpus and the key.",
        "",
        "- **query** — `search`, then `read_concept` on what it points at. "
        "`read_concept(\"index\")` returns the root index itself. `path` says which ranking "
        "answered — `embeddings`, `index-traversal`, or `embeddings+index-traversal`, where "
        "the two are alternated by rank and their scores are not comparable — and `note` "
        "says what the ranking could not see. **Read `note` when it is not empty**: a "
        "concept written since the last `okragged index` has no vector, and it is ranked by "
        "traversal instead rather than dropped.",
        "- **lint** — okflint conformance plus ghost edges (similar pairs nobody linked), "
        "stale concepts, and orphans (concepts nothing links to). Ghost edges are computed "
        "by `okragged index`, so run that if it reports having none to read.",
        "- **add_source** — for knowledge no document states: two things are related in a "
        "way no source says, and somebody knows it. Give it prose and it writes that prose "
        "into the corpus as a new source, generating the path itself. Every concept traces "
        "to a source, which is why nothing here writes a concept directly, nor a `verified` "
        "block — appending one is a separate tool, on the instances that offer it. Asserting "
        "that x relates to y will mint a third concept linking to both; "
        "it does not rewrite x and y to link back. Whether the concept arrives now or with "
        "the next `okragged build` is the mode difference above, and the result says which: "
        "`synthesized` whether a concept was authored, `concepts` what it was, and "
        "`indexed` whether `search` can rank it by embedding yet.",
        "",
        "## Registering the server",
        "",
        f"The server is `{COMMAND} mcp`, a subcommand of the console script — nothing to "
        "clone and no script to point at. It needs the MCP SDK, an optional extra: "
        f'`pip install "{COMMAND}[mcp]"`.',
        "",
        "The project this bundle was built in already registers it. To consume this "
        "bundle from another project, put this in that project's MCP config instead:",
        "",
        "```json",
        json.dumps(foreign, indent=2),
        "```",
        "",
        "**That entry is deliberately flagless, so the server it spawns is read-only.** "
        "Whoever produced this bundle may have registered a writing or a spending "
        "instance on their own machine; that is a fact about their machine and their "
        "key, not about yours. If you want the writing tool, add `--write` to the args "
        "above — and `--spend` only with a credential of your own, in this entry's "
        "`env`, because it is what lets the server author a concept and embed a query.",
        "",
        "Everything in this file is also served over the protocol, so a consumer that "
        f"never sees this directory still gets it: `{CONTRACT_URI}` is this contract and "
        f"`{INDEX_URI}` is `{bundle}/index.md`.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _structure(bundle: str, concepts: list[Concept]) -> list[str]:
    counts: Counter[str] = Counter()
    for concept in concepts:
        counts[posixpath.dirname(concept.concept_id)] += 1
    lines = [
        f"{bundle}/",
        "  index.md",
        "  log.md",
        f"  ({counts.get('', 0)} concept(s) at the root)",
    ]
    for directory in sorted(d for d in counts if d):
        indent = "  " * (directory.count("/") + 1)
        lines.append(f"{indent}{directory.rsplit('/', 1)[-1]}/  ({counts[directory]} concept(s))")
    return lines


def _types(concepts: list[Concept]) -> list[str]:
    counts = Counter(concept.type for concept in concepts)
    return [f"| `{name}` | {count} |" for name, count in sorted(counts.items())]


def _write(path: Path, text: str) -> Path:
    """Writes via temp file plus rename, same as `okf/writer.py` does for a concept.

    Matters more here than tidiness: `.mcp.json` holds other people's server
    registrations too, so a truncated write costs the user every server in
    the file, not just this one regenerable entry.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
