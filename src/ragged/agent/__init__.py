"""Layer 3 — the agent contract (DESIGN §10).

Reads the bundle and generates the contract for consuming it. `server` is
deliberately not imported here: it needs the optional MCP SDK, and `pipeline`
imports this package to regenerate the contract on every ingest.
"""

from .contract import (
    AGENTS_NAME,
    MCP_CONFIG_NAME,
    SERVER_NAME,
    render_agents_md,
    render_mcp_json,
    write_contract,
)
from .lint import LintReport, lint
from .query import EMBEDDING, TRAVERSAL, Hit, SearchResult, read_concept, search

__all__ = [
    "AGENTS_NAME",
    "EMBEDDING",
    "Hit",
    "LintReport",
    "MCP_CONFIG_NAME",
    "SERVER_NAME",
    "SearchResult",
    "TRAVERSAL",
    "lint",
    "read_concept",
    "render_agents_md",
    "render_mcp_json",
    "search",
    "write_contract",
]
