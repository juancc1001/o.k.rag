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


def register(config: Config) -> Path:
    """Write the `.mcp.json` entry, merged into whatever else is registered.

    The one function here that writes a file okragged does not own, which is why
    nothing calls it on your behalf: `okragged agent --register` asks for it, and
    a build never does.
    """
    return _write(config.project_root / MCP_CONFIG_NAME, render_mcp_json(config))


def is_registered(config: Config) -> bool:
    """Whether `.mcp.json` already registers this project's server.

    Only so a build can *mention* that it does not; a build that acted on this
    would be the side effect this module exists to remove.
    """
    path = config.project_root / MCP_CONFIG_NAME
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(loaded, dict) and SERVER_KEY in (loaded.get("mcpServers") or {})


def render_mcp_json(config: Config) -> str:
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
    servers[SERVER_KEY] = _entry(config.project_root, local=True)
    return json.dumps({**existing, "mcpServers": servers}, indent=2) + "\n"


def _entry(project_root: Path, *, local: bool) -> dict[str, object]:
    """The `mcpServers` value.

    A client spawns the server with the directory holding its config as the cwd,
    so an entry written into the project root needs no target — cwd already
    answers it. An entry copied anywhere else carries the root absolutely,
    because nothing about a foreign checkout can be said relative to this one,
    and it names the bare command for the same reason: this machine's resolution
    is not a fact about that one.
    """
    args = ["mcp"]
    if not local:
        args += ["--project", project_root.resolve().as_posix()]
    return {"command": _command() if local else COMMAND, "args": args}


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
        "Four tools, over the MCP server. **None of them needs a credential.**",
        "",
        "| tool | signature |",
        "|---|---|",
        "| `search` | `(query: str, k: int = 8)` → pointers: `concept_id`, `title`, "
        "`description`, `score` |",
        "| `read_concept` | `(concept_id: str)` → the raw file, frontmatter included |",
        "| `lint` | `()` → conformance, ghost edges, stale concepts, orphans |",
        "| `add_source` | `(text: str, title: str \\| None)` → the source file it wrote |",
        "",
        "**Nothing here calls a model, so nothing here spends anybody's credential.** "
        "Querying and linting read the bundle; `add_source` writes a file. The one step "
        "that costs money — turning a source into a concept — is `okragged build`, run by "
        "whoever owns the corpus and the key.",
        "",
        "- **query** — `search`, then `read_concept` on what it points at. "
        "`read_concept(\"index\")` returns the root index itself.",
        "- **lint** — okflint conformance plus ghost edges (similar pairs nobody linked), "
        "stale concepts, and orphans (concepts nothing links to). Ghost edges are computed "
        "by `okragged index`, so run that if it reports having none to read.",
        "- **add_source** — for knowledge no document states: two things are related in a "
        "way no source says, and somebody knows it. Give it prose and it writes that prose "
        "into the corpus as a new source, generating the path itself. **It records only** — "
        "the concept is authored by the next `okragged build`, so do not wait for one or "
        "report that it appeared. Every concept traces to a source, which is why nothing "
        "here writes a concept — or a `verified` block — directly. Asserting that x relates "
        "to y will mint a third concept linking to both; it does not rewrite x and y to "
        "link back.",
        "",
        "**Synthesis is not on this surface**, deliberately: it is `okragged build`, where "
        "the sources and the credential already are, run by whoever owns them.",
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
