"""The read side of DESIGN §11, over HTTP.

Four endpoints, every one of them offline and read-only. Each wraps capability
that already exists — `agent/query.py`, `agent/lint.py`, and the `graph.json`
`okragged index` wrote — so `search` and `lint` cannot drift from the MCP tools
that answer the same questions (§10). Nothing here writes anywhere.

`POST /api/ingest` and its SSE progress stream are the two endpoints §11 lists
that are not here. They needed two things the pipeline did not have: progress
events, which now exist — `ingest` takes a `progress` callable and reports each
stage as it reaches it — and a job model, which does not. The graph view needs
neither, so `ingest` stays on the CLI and the MCP server until something asks
for it over HTTP.

FastAPI is an optional extra for the same reason the MCP SDK is — producing and
validating a bundle needs neither.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import okf
from ..agent.lint import lint as run_lint
from ..agent.query import read_concept as read_one
from ..agent.query import search as run_search
from ..config import Config
from ..vectors import GRAPH_NAME

STATIC_DIR = Path(__file__).parent / "static"
"""The built frontend, mounted at `/` when it exists.

**Resolved from the package, never from the project root.** It used to be
`config.project_root / "web/dist"` — inside the *user's corpus directory*, beside
their `sources/`, which is only correct when the project root happens to be this
checkout. An installed user who built the frontend would have had to copy it next
to their own corpus to be served it. That is the defect class §10 and M5 exist to
remove: a path that means something only inside the producer's tree. Vite's
`outDir` points here, so one build serves the checkout and the wheel alike.

In dev it is Vite that serves the app and proxies `/api` here, so this staying
absent is normal.
"""


def create_app(config: Config):
    try:
        from fastapi import FastAPI, HTTPException, Response
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - exercised by the extra being absent
        raise RuntimeError(
            'the HTTP API needs the optional extra: pip install -e ".[api]"'
        ) from exc

    app = FastAPI(
        title="okragged",
        description="Read-only HTTP access to an OKF bundle and its graph.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.get("/api/graph")
    def graph() -> Response:
        """`.okragged/graph.json` verbatim: nodes, links, ghosts, stats."""
        path = config.sidecar_root / GRAPH_NAME
        if not path.exists():
            raise HTTPException(404, f"no {GRAPH_NAME}; run `okragged index`")
        # served as the bytes on disk rather than parsed and re-serialized, so
        # what the frontend renders is what `okragged index` wrote
        return Response(path.read_text(encoding="utf-8-sig"), media_type="application/json")

    @app.get("/api/concept/{concept_id:path}")
    def concept(concept_id: str) -> dict[str, Any]:
        """One concept: its frontmatter, its body, and the body rendered."""
        try:
            raw = read_one(config, concept_id)
        except (LookupError, OSError) as exc:
            raise HTTPException(404, str(exc)) from None
        try:
            parsed = okf.parse(raw, concept_id)
        except ValueError as exc:
            # an index.md or a malformed file: reachable, but not a concept
            raise HTTPException(404, str(exc)) from None
        return {
            "concept_id": parsed.concept_id,
            "frontmatter": parsed.frontmatter(),
            "body": parsed.body,
            "html": render_html(parsed.body),
        }

    @app.get("/api/search")
    def search(q: str, k: int = 8) -> dict[str, Any]:
        """Pointers, never bodies — the same two paths as the MCP tool (§10)."""
        result = run_search(config, q, k)
        return {
            "path": result.path,
            "note": result.note,
            "results": [asdict(hit) for hit in result.hits],
        }

    @app.get("/api/lint")
    def lint() -> dict[str, Any]:
        """Conformance, ghost edges, stale concepts, orphans."""
        return asdict(run_lint(config))

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")

    return app


_MARKDOWN = None


def render_html(body: str) -> str:
    """CommonMark with raw HTML escaped rather than passed through.

    `html=False` is load-bearing, not stylistic: the body is model-written, and
    the `commonmark` preset enables raw HTML, so the panel would render whatever
    a source document talked the model into emitting.
    """
    global _MARKDOWN
    if _MARKDOWN is None:
        from markdown_it import MarkdownIt

        _MARKDOWN = MarkdownIt("commonmark", {"html": False})
    return _MARKDOWN.render(body)
