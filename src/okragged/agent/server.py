"""The stdio MCP server (DESIGN §10).

Four tools over capability that already exists: `search` and `read_concept` from
`agent/query.py`, `lint` from `agent/lint.py`, `add_source` from `agent/write.py`
— which goes through `pipeline.ingest`, whose `_finalize` runs in a `finally`, so
a client that disconnects mid-run cannot strand model calls already paid for.

**There is no `ingest` tool, and its absence is the point** (§13 M5). A full
sweep runs `_deprecate_vanished`, so a recipient holding the bundle and the
sidecar but no corpus would have every concept marked `deprecated` — which is
exactly what you would ship to make search work. Removing the tool closes that
structurally; gating it would only guard it.

The tool descriptions carry the rules that matter, because they are the only
channel every client shows a model: `instructions` are client-dependent and
resources are an optional client capability, but a tool is displayed with its
description wherever tools are.

Plus two resources, which is how the contract reaches a consumer that is not
standing in this tree: the handshake `instructions` are short and name no path,
and the contract itself is fetched over the protocol. Both handlers call the
functions that already render what is on disk, so what is served cannot drift
from the file.

**Nothing here may write to stdout.** The protocol is newline-delimited JSON on
it. The SDK's stdio transport serves the wire from a private duplicate of fd 1
and points fd 1 itself at stderr, so a stray `print` — markitdown's ffmpeg
warning, say — misses the wire rather than corrupting a frame; this module still
prints nothing of its own.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import Config
from ..config import load as load_config
from .contract import CONTRACT_URI, INDEX_URI, render_agents_md
from .lint import lint as run_lint
from .query import read_concept as read_one
from .query import search as run_search
from .write import add_source as run_add_source

INSTRUCTIONS = (
    "An Open Knowledge Format bundle: markdown concepts with YAML frontmatter, one "
    f"index per directory, and a cross-link graph. Read the `{CONTRACT_URI}` resource "
    "first — it says what this bundle holds and how to read it — then work from "
    f"`{INDEX_URI}`. `search` returns pointers, never bodies; `read_concept` returns a "
    "whole file, which is the unit of knowledge here."
)
"""Asserted across a process boundary, so it names no path: the consumer reading
it works in its own tree and has none of this one's (DESIGN §13 M5)."""


def build_server(config: Config):
    server = _mcp_server()(name="okragged", instructions=INSTRUCTIONS)

    @server.resource(
        CONTRACT_URI,
        name="contract",
        title="Agent contract",
        description="What this bundle holds, how to query it, and what its trust tiers mean.",
        mime_type="text/markdown",
    )
    def contract() -> str:
        return render_agents_md(config)

    @server.resource(
        INDEX_URI,
        name="index",
        title="Bundle index",
        description="The bundle's root index: every top-level concept, with its own "
        "title and description.",
        mime_type="text/markdown",
    )
    def index() -> str:
        return read_one(config, "index")

    @server.tool()
    def search(query: str, k: int = 8) -> dict[str, Any]:
        """Find concepts. Returns pointers - id, title, description, score - never bodies.

        A way in, not a substitute for the indexes: read the `okragged://index`
        resource first, then read what either one points at.
        """
        result = run_search(config, query, k)
        return {
            "path": result.path,
            "note": result.note,
            "results": [asdict(hit) for hit in result.hits],
        }

    @server.tool()
    def read_concept(concept_id: str) -> str:
        """Read one concept file whole, frontmatter included.

        A concept file is the unit of knowledge here - read the whole file, never
        a fragment of one.
        """
        return read_one(config, concept_id)

    @server.tool()
    def add_source(text: str, title: str | None = None) -> dict[str, Any]:
        """Record knowledge no source document states, as a new source.

        Takes prose, not a concept: it is written into the corpus as a source
        file - the path is generated here and never accepted - and `okragged
        build` authors the concept from it later, so the concept has real
        provenance. Submit no frontmatter. Nothing on this surface writes a
        `verified` block: that records a confirmation event that actually
        happened, and a human promotes a concept by editing the file.

        This records only. **No concept appears until someone runs `okragged
        build`**, which is deliberate: synthesis costs money, and nothing
        reachable over this protocol may spend somebody's credential. Say so
        rather than waiting for a concept that is not coming. `recorded: false`
        means identical prose was already on file, so nothing was written.
        """
        return asdict(run_add_source(config, text, title))

    @server.tool()
    def lint() -> dict[str, Any]:
        """Conformance, ghost edges, stale concepts and orphans. Offline."""
        return asdict(run_lint(config))

    return server


def main(project_root: Path) -> None:
    from dotenv import load_dotenv

    config = load_config(project_root)
    # utf-8-sig: a BOM otherwise renames the first key and the credential reads
    # as missing, which `add_source` would report as a configuration problem
    load_dotenv(project_root / ".env", encoding="utf-8-sig")
    build_server(config).run()


def _mcp_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError(
            "the MCP server needs the optional extra: pip install -e \".[mcp]\""
        ) from exc
    return MCPServer
