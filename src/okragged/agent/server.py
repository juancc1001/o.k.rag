"""The stdio MCP server (DESIGN §10).

Four tools over capability that already exists: `search` and `read_concept` from
`agent/query.py`, `lint` from `agent/lint.py`, `ingest` straight through
`pipeline.ingest` — whose `_finalize` runs in a `finally`, so a client that
disconnects mid-run cannot strand model calls already paid for.

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
from ..pipeline import IngestSummary
from ..pipeline import ingest as run_ingest
from .lint import lint as run_lint
from .query import read_concept as read_one
from .query import search as run_search

INSTRUCTIONS = (
    "An Open Knowledge Format bundle. Read index.md first, then read whole concept "
    "files — `search` returns pointers, not bodies. agent/AGENTS.md is the contract."
)


def build_server(config: Config):
    server = _mcp_server()(name="okragged", instructions=INSTRUCTIONS)

    @server.tool()
    def search(query: str, k: int = 8) -> dict[str, Any]:
        """Find concepts. Returns pointers - id, title, description, score - never bodies."""
        result = run_search(config, query, k)
        return {
            "path": result.path,
            "note": result.note,
            "results": [asdict(hit) for hit in result.hits],
        }

    @server.tool()
    def read_concept(concept_id: str) -> str:
        """Read one concept file whole, frontmatter included."""
        return read_one(config, concept_id)

    @server.tool()
    def ingest(paths: list[str] | None = None) -> dict[str, Any]:
        """Re-ingest sources into the bundle. null = every source. Needs a credential."""
        return _ingest_payload(run_ingest(config, only=paths))

    @server.tool()
    def lint() -> dict[str, Any]:
        """Conformance, ghost edges, stale concepts and orphans. Offline."""
        return asdict(run_lint(config))

    return server


def main(project_root: Path) -> None:
    from dotenv import load_dotenv

    config = load_config(project_root)
    # utf-8-sig: a BOM otherwise renames the first key and the credential reads
    # as missing, which `ingest` would report as a configuration problem
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


def _ingest_payload(summary: IngestSummary) -> dict[str, Any]:
    report = summary.report
    return {
        "concepts": sum(outcome.concept_count for outcome in summary.synthesized),
        "written": [
            {
                "uri": outcome.uri,
                "concept_id": outcome.concept_id,
                "children": outcome.child_ids,
                "section_errors": outcome.section_errors,
                "weave_errors": outcome.weave_errors,
                "removed": outcome.removed,
            }
            for outcome in summary.synthesized
        ],
        "skipped": [outcome.uri for outcome in summary.skipped],
        "failed": [
            {"uri": outcome.uri, "error": outcome.error} for outcome in summary.failed
        ],
        "protected": {
            cid: why for outcome in summary.outcomes for cid, why in outcome.protected.items()
        },
        "deprecated": summary.deprecated,
        "new_types": sorted({o.new_type for o in summary.outcomes if o.new_type}),
        "tokens": {
            "input": summary.input_tokens,
            "output": summary.output_tokens,
            "cache_read": summary.cache_read_tokens,
        },
        "conformant": report.ok if report else None,
        "errors": [str(finding) for finding in report.errors] if report else [],
    }
