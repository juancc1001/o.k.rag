"""The HTTP API (DESIGN §11).

Four read-only endpoints. `search` and `lint` are the same functions the MCP
tools call, so what is tested here is the HTTP boundary — status codes, the
shape that reaches the frontend, and that a read cannot leave the bundle — not
the ranking, which `test_agent.py` owns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from okrag import config as config_module, okf
from okrag.api.app import create_app
from okrag.vectors import GRAPH_NAME

TOML = """\
[bundle]
root = "bundle"
okf_version = "0.2"
actor = "okrag/test"
title = "Test Bundle"

[sources.local]
path = "sources"
include = ["**/*.md"]

[types]
allowed = ["Policy", "Reference"]

[vectors]
provider = "gemini"
dim = 3
"""

CONCEPTS = [
    dict(
        concept_id="revenue-policy",
        type="Policy",
        title="Revenue Recognition Policy",
        description="When revenue is recognized and how prepayments are deferred.",
        body="Revenue is recognized on delivery. See the [Price List](/pricing.md).",
    ),
    dict(
        concept_id="pricing",
        type="Reference",
        title="Price List",
        description="What each plan costs.",
        body="Plans are billed annually.",
    ),
]

GRAPH = {
    "nodes": [
        {
            "id": "revenue-policy",
            "title": "Revenue Recognition Policy",
            "type": "Policy",
            "status": "draft",
            "trust": "unverified",
            "stale": False,
            "x": 0.5,
            "y": -1.25,
            "degree": 1,
        },
        {
            "id": "pricing",
            "title": "Price List",
            "type": "Reference",
            "status": "draft",
            "trust": "unverified",
            "stale": False,
            "x": -0.5,
            "y": 1.25,
            "degree": 1,
        },
    ],
    "links": [{"source": "revenue-policy", "target": "pricing", "kind": "explicit"}],
    "ghosts": [],
    "stats": {"concepts": 2, "links": 1, "ghosts": 0, "broken_links": 0},
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "okrag.toml").write_text(TOML, encoding="utf-8", newline="\n")
    bundle = tmp_path / "bundle"
    for fields in CONCEPTS:
        okf.write_concept(bundle, okf.Concept(status="draft", **fields))
    okf.write_indexes(bundle, "0.2", "Test Bundle")
    return tmp_path


def client(project: Path):
    from fastapi.testclient import TestClient

    return TestClient(create_app(config_module.load(project)))


def write_graph(project: Path, graph: dict | None = None) -> Path:
    path = project / ".okrag" / GRAPH_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(graph if graph is not None else GRAPH, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def bundle_bytes(project: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted((project / "bundle").rglob("*")) if p.is_file()}


# --- graph -----------------------------------------------------------------------


def test_the_graph_endpoint_serves_exactly_what_the_index_wrote(project):
    path = write_graph(project)
    response = client(project).get("/api/graph")
    assert response.status_code == 200
    assert response.json() == json.loads(path.read_text(encoding="utf-8-sig"))


def test_the_graph_endpoint_says_to_run_index_when_there_is_no_graph(project):
    response = client(project).get("/api/graph")
    assert response.status_code == 404
    assert "okrag index" in response.json()["detail"]


def test_the_graph_carries_both_edge_layers_the_view_renders(project):
    write_graph(
        project,
        {
            **GRAPH,
            "ghosts": [
                {
                    "source": "pricing",
                    "target": "revenue-policy",
                    "kind": "semantic",
                    "similarity": 0.91,
                }
            ],
        },
    )
    graph = client(project).get("/api/graph").json()
    assert [link["kind"] for link in graph["links"]] == ["explicit"]
    assert [ghost["kind"] for ghost in graph["ghosts"]] == ["semantic"]


# --- concept ---------------------------------------------------------------------


def test_a_concept_comes_back_with_its_frontmatter_and_its_body_rendered(project):
    payload = client(project).get("/api/concept/pricing").json()
    assert payload["concept_id"] == "pricing"
    assert payload["frontmatter"]["title"] == "Price List"
    assert payload["frontmatter"]["type"] == "Reference"
    assert payload["body"] == "Plans are billed annually."
    assert "<p>Plans are billed annually.</p>" in payload["html"]


def test_a_concept_in_a_subdirectory_is_addressable_by_its_whole_id(project):
    okf.write_concept(
        project / "bundle",
        okf.Concept(concept_id="finance/fees", type="Policy", title="Fees", body="Fees apply."),
    )
    response = client(project).get("/api/concept/finance/fees")
    assert response.status_code == 200
    assert response.json()["concept_id"] == "finance/fees"


def test_a_concept_id_that_escapes_the_bundle_is_not_served(project):
    (project / "secret.md").write_text("---\ntype: Policy\n---\n\nx\n", encoding="utf-8", newline="\n")
    for attempt in ("../secret", "..%2Fsecret"):
        assert client(project).get(f"/api/concept/{attempt}").status_code == 404


def test_an_index_is_reachable_but_is_not_served_as_a_concept(project):
    response = client(project).get("/api/concept/index")
    assert response.status_code == 404
    assert "index" in response.json()["detail"]


def test_a_concept_that_does_not_exist_is_a_404_rather_than_a_traceback(project):
    assert client(project).get("/api/concept/nope").status_code == 404


def test_raw_html_in_a_body_is_escaped_rather_than_rendered(project):
    okf.write_concept(
        project / "bundle",
        okf.Concept(
            concept_id="injected",
            type="Policy",
            title="Injected",
            body="Text <script>alert(1)</script> more.",
        ),
    )
    html = client(project).get("/api/concept/injected").json()["html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- search ----------------------------------------------------------------------


def test_search_over_http_returns_pointers_and_never_a_body(project):
    payload = client(project).get("/api/search", params={"q": "revenue"}).json()
    assert payload["results"]
    assert set(payload["results"][0]) == {"concept_id", "title", "description", "score"}
    assert all("body" not in hit for hit in payload["results"])


def test_search_over_http_reports_which_path_answered(project):
    payload = client(project).get("/api/search", params={"q": "revenue"}).json()
    assert payload["path"] == "index-traversal"
    assert "okrag index" in payload["note"]


def test_search_honours_k(project):
    payload = client(project).get("/api/search", params={"q": "revenue", "k": 1}).json()
    assert len(payload["results"]) == 1


def test_search_without_a_query_is_a_422_rather_than_the_whole_bundle(project):
    assert client(project).get("/api/search").status_code == 422


# --- lint ------------------------------------------------------------------------


def test_lint_over_http_reports_conformance_with_no_credential(project):
    payload = client(project).get("/api/lint").json()
    assert payload["concepts"] == 2
    assert payload["errors"] == []


def test_lint_over_http_reports_the_concepts_nothing_links_to(project):
    assert client(project).get("/api/lint").json()["orphans"] == ["revenue-policy"]


def test_lint_says_why_it_has_no_ghost_edges_until_the_index_has_run(project):
    payload = client(project).get("/api/lint").json()
    assert payload["ghosts"] == []
    assert "okrag index" in payload["ghosts_note"]


# --- the boundary ----------------------------------------------------------------


def test_nothing_the_api_serves_writes_into_the_bundle(project):
    write_graph(project)
    before = bundle_bytes(project)
    api = client(project)
    api.get("/api/graph")
    api.get("/api/concept/pricing")
    api.get("/api/search", params={"q": "revenue"})
    api.get("/api/lint")
    assert bundle_bytes(project) == before


def test_the_view_is_served_only_once_it_has_been_built(project, tmp_path, monkeypatch):
    from okrag.api import app as api_app

    built = tmp_path / "static"
    monkeypatch.setattr(api_app, "STATIC_DIR", built)
    assert client(project).get("/").status_code == 404

    built.mkdir()
    (built / "index.html").write_text("<h1>graph</h1>", encoding="utf-8", newline="\n")
    assert "graph" in client(project).get("/").text


def test_the_built_view_is_found_in_the_package_and_never_in_the_users_corpus(project):
    """The M6 defect: `project_root / "web/dist"` put the frontend inside whatever
    corpus directory `okrag serve` was pointed at, so only a run from this
    checkout could ever find it. It is package-relative now, which is the one
    location that is true for an installed user."""
    from okrag.api import app as api_app
    from okrag.api.app import STATIC_DIR

    assert STATIC_DIR == Path(api_app.__file__).parent / "static"
    assert not STATIC_DIR.is_relative_to(project), "never inside the user's corpus"

    # and a corpus that happens to contain one is still not consulted
    decoy = project / "web" / "dist"
    decoy.mkdir(parents=True)
    (decoy / "index.html").write_text("<h1>decoy</h1>", encoding="utf-8", newline="\n")
    assert "decoy" not in client(project).get("/").text
