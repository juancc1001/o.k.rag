"""Layer 3 — the agent contract (DESIGN §10).

Reads the bundle and generates the contract for consuming it. `server` is
deliberately not imported here: it needs the optional MCP SDK, and `pipeline`
imports this package to regenerate the contract on every ingest.
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
