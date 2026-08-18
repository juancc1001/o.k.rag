"""Layer 3: the generated agent contract (DESIGN §2, §10).

Two artifacts with **different natures**, which is why they are written by two
functions and not one:

`agent/AGENTS.md` is a *function of the bundle* — the concept count, the
structure tree, the type vocabulary. It changes whenever the bundle changes, so
`write_contract` runs after every commit, and §10's rule that the contract must
never describe a bundle that no longer exists is a rule about this file. It
carries no timestamp, so an unchanged bundle regenerates to the same bytes.

`.mcp.json` is not a function of the bundle at all. The entry is the same bytes
whether the bundle holds no concepts or a thousand. Under M4 it named the
generating interpreter and the generated `agent/mcp_server.py`, so it really was
derived from the producer's tree; the console script deleted that, and rewriting
it on every build outlived its reason. So `register` is a **deliberate act** —
`okragged agent --register` — and never a side effect of building.

What it *is* a function of is the machine, and only where it has to be. The
entry names `okragged` whenever `PATH` can resolve it, and falls back to the
console script beside this interpreter when it cannot — the same exposure
`okf/validate.py::_okflint` documents, one layer up. A client spawns the server
from its `.mcp.json` rather than from an activated shell, so a bare name that
only an activated venv resolves gets the server killed at spawn and reported as
a closed connection, with nothing to read anywhere. `PATH` first rather than
beside-first, because this file is not gitignored and may be shared: portable
when portability is available, correct when it is not.

The distinction is not tidiness. `.mcp.json` is the one thing here okragged does
not own: it is the MCP config for whatever agent works in that directory, shared
with every other server registered there. Merging into somebody else's config
unasked, on every build, is a write okragged has no reason to make.
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
"""The console script. Not an interpreter and not a script path: the consumer a
documented entry exists for is in another tree entirely, and only `_command`
may substitute an absolute one, only for the local file."""

CONTRACT_URI = "okragged://contract"
INDEX_URI = "okragged://index"
"""The two resources the server exports (§10). Named here rather than in
`server.py` so the contract can quote them without the MCP SDK installed."""


def write_contract(config: Config) -> list[Path]:
    """Regenerate `agent/AGENTS.md`. Does **not** register the server.

    A list of one, because the caller reports what was written and because the
    contract may yet grow a second derived file — registration will not be it.
    """
    return [_write(config.project_root / "agent" / AGENTS_NAME, render_agents_md(config))]


def register(config: Config, *, write: bool = False, spend: bool = False) -> Path:
    """Write the `.mcp.json` entry, merged into whatever else is registered.

    The one function here that writes a file okragged does not own, which is why
    nothing calls it on your behalf: `okragged agent --register` asks for it, and
    a build never does.

    The switches go into the entry when they are asked for, and they are asked for
    in the same breath as the registration itself — `--register --write --spend`
    names precisely the instance it wants, so nothing is inferred and nothing is
    decided for anybody. Omitted, the entry is bare and the server it spawns is
    read-only, which is the default that matters: the file is shared, and an entry
    that could spend by accident is what the switches exist to prevent.
    """
    return _write(
        config.project_root / MCP_CONFIG_NAME,
        render_mcp_json(config, write=write, spend=spend),
    )


def is_registered(config: Config) -> bool:
    """Whether `.mcp.json` already registers this project's server.

    **The key, not the argument vector.** An operator may have registered the
    server with `--write` or `--spend` in its `args`, or added them by hand
    afterwards; comparing the whole entry would read that as unregistered and
    have every build tell them to register a server that is already there.

    Only so a build can *mention* that it does not; a build that acted on this
    would be the side effect this module exists to remove.
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
    """The `mcpServers` value.

    A client spawns the server with the directory holding its config as the cwd,
    so an entry written into the project root needs no target — cwd already
    answers it. An entry copied anywhere else carries the root absolutely,
    because nothing about a foreign checkout can be said relative to this one,
    and it names the bare command for the same reason: this machine's resolution
    is not a fact about that one.

    **The switches reach the local entry only.** The copy in `AGENTS.md` is for
    somebody else's config, and what this operator authorized on this machine is
    not a fact about that one — a snippet carrying `--spend` would be telling a
    stranger to spend a key. A consumer who wants to write adds them knowingly,
    which the contract says in prose beside the flagless JSON.
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
    """`PATH` first, then the console script beside this interpreter.

    The registration is spawned by a client, not from an activated shell, so
    `okragged` installed into a venv is invisible to the spawn and the whole
    server is lost to a bare `CONNECTION_CLOSED`. Beside `sys.executable` is
    where that install actually sits (`okf/validate.py::_okflint`, same reason).
    Tried second rather than first because `.mcp.json` is not gitignored: a
    resolvable name travels to another checkout and an absolute one does not.
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
        "**Never write a `verified` block.** It records a confirmation event that actually "
        "happened; writing one because the content looks correct forges the human-reviewed "
        "tier. okragged never writes it and neither should you — a human promotes a concept "
        "by editing the file.",
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
        "to a source, which is why nothing here writes a concept — or a `verified` block — "
        "directly. Asserting that x relates to y will mint a third concept linking to both; "
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
    """Temp file plus rename, as `okf/writer.py` does for a concept.

    Load-bearing for `.mcp.json` and not merely tidy: it is read, merged and
    written back, and it holds other people's server registrations. A truncated
    write there costs the user every other server in the file, not one
    regenerable artifact of ours.
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
