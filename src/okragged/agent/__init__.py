"""The agent contract: how an AI agent discovers and reads this bundle
(`AGENTS.md`, the MCP server, the HTTP API) — DESIGN.md §10 calls this "layer
3", one level up from the bundle itself ("layer 2").

This module reads the bundle and generates the contract describing it.
`server` is deliberately not imported here: it needs the optional MCP SDK, and
`pipeline` imports this package on every ingest just to regenerate the contract.
"""

from .contract import (
    AGENTS_NAME,
    CONTRACT_URI,
    INDEX_URI,
    MCP_CONFIG_NAME,
    is_registered,
    register,
    render_agents_md,
    render_mcp_json,
    write_contract,
)
from .lint import GHOST_LIMIT, LintReport, lint
from .query import EMBEDDING, MERGED, TRAVERSAL, Hit, SearchResult, read_concept, search

__all__ = [
    "AGENTS_NAME",
    "CONTRACT_URI",
    "EMBEDDING",
    "GHOST_LIMIT",
    "INDEX_URI",
    "Hit",
    "LintReport",
    "MCP_CONFIG_NAME",
    "MERGED",
    "SearchResult",
    "TRAVERSAL",
    "is_registered",
    "lint",
    "read_concept",
    "register",
    "render_agents_md",
    "render_mcp_json",
    "search",
    "write_contract",
]
