"""The agent contract (DESIGN §10): the four tools, and the generated AGENTS.md.

Three of the four tools must work with no credential at all — that is the M4 exit
criterion — so nothing here sets one except where the embedding path is the thing
under test, and the embedding provider is stubbed in a toy space either way.

The M5 invariant is here too: what layer 3 says across the process boundary may
not name a path relative to the producer's root. `producer_paths` is the check,
and it has a meta-test pointing it at the text it replaced.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

from okragged import agent, config as config_module, okf, pipeline
from okragged.agent import contract
from okragged.synth import MissingCredential, SynthResult, WeaveResult
from okragged.vectors import EmbedError
from okragged.vectors.providers import gemini

TOML = """\
[bundle]
root = "bundle"
okf_version = "0.2"
actor = "okragged/test"
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
    dict(
        concept_id="holidays",
        type="Reference",
        title="Office Holidays",
        description="Days the office is closed.",
        body="Nobody recognizes revenue on a holiday.",
    ),
    dict(
        concept_id="reembolsos",
        type="Policy",
        title="Política de Reembolsos",
        description="Cuándo se devuelve el dinero.",
        body="El reembolso procede en treinta días.",
    ),
]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "okragged.toml").write_text(TOML, encoding="utf-8", newline="\n")
    bundle = tmp_path / "bundle"
    for fields in CONCEPTS:
        okf.write_concept(bundle, okf.Concept(status="draft", **fields))
    okf.write_indexes(bundle, "0.2", "Test Bundle")
    return tmp_path


def cfg(project: Path):
    return config_module.load(project)


def write_graph(project: Path, graph: dict) -> None:
    sidecar = project / ".okragged"
    sidecar.mkdir(parents=True, exist_ok=True)
    (sidecar / "graph.json").write_text(
        json.dumps(graph), encoding="utf-8", newline="\n"
    )


def bundle_bytes(project: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted((project / "bundle").rglob("*")) if p.is_file()}


PRODUCER_PATH = re.compile(
    r"""
    (?<![:\w/\\])(?:[\w.-]+[/\\])+[\w.-]+      # a path: at least one directory segment
    | (?<![\w:/\\])[A-Za-z]:[/\\][^\s`'"]*     # a drive-lettered absolute path
    | (?<![\w./\\])(?:\.mcp\.json|okragged\.toml|AGENTS\.md)\b   # producer-root files
    """,
    re.VERBOSE,
)


def producer_paths(text: str) -> list[str]:
    """Every token in `text` that addresses a file rather than naming a resource.

    A URI is not a path: `okragged://contract` survives this, which is the whole
    point of exporting the contract over the protocol instead of pointing at it.
    A bare filename survives too — `index.md` describes the bundle's own shape and
    is reached with `read_concept`, not opened.
    """
    return [match.group() for match in PRODUCER_PATH.finditer(text)]


def fake_embed(chunks, *, model, dim):
    """A toy space with two directions: prepayments, and everything else."""
    return [
        [1.0, 0.0, 0.0] if "prepayment" in text.lower() else [0.0, 1.0, 0.0] for text in chunks
    ]


# --- search ----------------------------------------------------------------------


def test_search_returns_pointers_and_never_a_body(project):
    result = agent.search(cfg(project), "revenue recognition", k=8)
    top = result.hits[0]
    assert (top.concept_id, top.title) == ("revenue-policy", "Revenue Recognition Policy")
    assert top.description == "When revenue is recognized and how prepayments are deferred."
    assert not hasattr(top, "body")
    for hit in result.hits:
        assert "Revenue is recognized on delivery" not in (hit.description or "")


def test_search_with_no_credential_at_all_answers_by_traversing_the_bundle(project):
    result = agent.search(cfg(project), "revenue recognition", k=8)
    assert result.path == agent.TRAVERSAL
    assert "okragged index" in result.note
    assert [hit.concept_id for hit in result.hits][:1] == ["revenue-policy"]


def test_a_title_match_outranks_the_same_word_in_a_body(project):
    ranked = [hit.concept_id for hit in agent.search(cfg(project), "revenue", k=8).hits]
    assert ranked.index("revenue-policy") < ranked.index("holidays")


def test_a_concept_linked_from_a_hit_outranks_an_unrelated_body_mention(project):
    """`pricing` says nothing about revenue; the bundle links it from what does."""
    ranked = [hit.concept_id for hit in agent.search(cfg(project), "revenue recognition").hits]
    assert ranked.index("pricing") < ranked.index("holidays")


def test_an_unaccented_query_still_matches_an_accented_title(project):
    result = agent.search(cfg(project), "politica de reembolsos", k=3)
    assert result.hits[0].concept_id == "reembolsos"


def test_a_body_linking_the_same_concept_twice_does_not_boost_it_twice(project):
    """A link asserts a relationship; naming it again asserts the same one."""

    def pricing_score(body: str) -> float:
        okf.write_concept(
            project / "bundle",
            okf.Concept(
                concept_id="hub",
                type="Policy",
                title="Hub",
                description="Revenue hub.",
                body=body,
            ),
        )
        hits = agent.search(cfg(project), "revenue", k=8).hits
        return next(hit.score for hit in hits if hit.concept_id == "pricing")

    once = pricing_score("Revenue matters. See [Price List](/pricing.md).")
    thrice = pricing_score(
        "Revenue matters. See [Price List](/pricing.md), then [Price List](/pricing.md), "
        "then [Price List](/pricing.md)."
    )
    assert once == thrice


def test_a_query_matching_nothing_returns_no_pointers_rather_than_the_whole_bundle(project):
    assert agent.search(cfg(project), "hydroelectric turbine").hits == []


def test_search_returns_at_most_k_pointers(project):
    assert len(agent.search(cfg(project), "the", k=2).hits) <= 2


def test_search_uses_the_stored_vectors_when_a_credential_can_embed_the_query(
    project, monkeypatch
):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(gemini, "embed", fake_embed)
    from okragged import vectors

    vectors.index(cfg(project))

    result = agent.search(cfg(project), "prepayments", k=2)
    assert result.path == agent.EMBEDDING
    assert result.hits[0].concept_id == "revenue-policy"
    assert result.hits[0].score == pytest.approx(1.0, abs=1e-3)


def test_a_query_that_cannot_be_embedded_falls_back_to_traversal(project, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(gemini, "embed", fake_embed)
    from okragged import vectors

    vectors.index(cfg(project))

    def refuse(chunks, *, model, dim):
        raise EmbedError("gemini HTTP 429: quota")

    monkeypatch.setattr(gemini, "embed", refuse)
    result = agent.search(cfg(project), "prepayments", k=2)
    assert result.path == agent.TRAVERSAL
    assert "429" in result.note
    assert result.hits[0].concept_id == "revenue-policy"


# --- read_concept ----------------------------------------------------------------


def test_read_concept_returns_the_file_verbatim_frontmatter_included(project):
    text = agent.read_concept(cfg(project), "revenue-policy")
    assert text == (project / "bundle" / "revenue-policy.md").read_text(encoding="utf-8-sig")
    assert text.startswith("---\ntype: Policy")


def test_read_concept_reads_an_index_so_an_agent_can_start_there(project):
    assert 'okf_version: "0.2"' in agent.read_concept(cfg(project), "index")


def test_read_concept_accepts_the_id_as_the_indexes_write_it(project):
    assert agent.read_concept(cfg(project), "/pricing.md").startswith("---")


def test_read_concept_refuses_a_path_that_escapes_the_bundle(project):
    (project / "secrets.md").write_text("nope", encoding="utf-8", newline="\n")
    with pytest.raises(LookupError):
        agent.read_concept(cfg(project), "../secrets")


# --- lint ------------------------------------------------------------------------


def test_lint_reports_conformance_with_no_credential(project):
    report = agent.lint(cfg(project))
    assert report.conformant or report.okflint_note  # okflint may not be installed
    assert report.concepts == 4
    assert report.errors == []


def test_lint_reports_the_concepts_nothing_links_to(project):
    orphans = agent.lint(cfg(project)).orphans
    assert "pricing" not in orphans  # revenue-policy links to it
    assert {"holidays", "reembolsos", "revenue-policy"} <= set(orphans)


def test_lint_reports_a_concept_past_its_stale_after(project):
    okf.write_concept(
        project / "bundle",
        okf.Concept(
            concept_id="pricing",
            type="Reference",
            title="Price List",
            description="What each plan costs.",
            body="Plans are billed annually.",
            stale_after="2000-01-01",
        ),
    )
    assert agent.lint(cfg(project)).stale == ["pricing"]


def test_lint_says_why_it_has_no_ghost_edges_until_the_index_has_run(project):
    report = agent.lint(cfg(project))
    assert report.ghosts == []
    assert "okragged index" in report.ghosts_note


def test_lint_reports_the_ghost_edges_the_index_computed(project):
    sidecar = project / ".okragged"
    sidecar.mkdir(parents=True, exist_ok=True)
    (sidecar / "graph.json").write_text(
        json.dumps({"ghosts": [{"source": "holidays", "target": "pricing", "similarity": 0.9}]}),
        encoding="utf-8",
        newline="\n",
    )
    report = agent.lint(cfg(project))
    assert report.ghosts == [{"source": "holidays", "target": "pricing", "similarity": 0.9}]
    assert report.ghosts_note == ""


def test_lint_caps_the_ghost_edges_and_says_how_many_it_left_out(project):
    ghosts = [
        {"source": f"a{i}", "target": f"b{i}", "kind": "semantic", "similarity": (800 + i) / 1000}
        for i in range(25)
    ]
    write_graph(project, {"ghosts": ghosts})
    report = agent.lint(cfg(project))

    assert len(report.ghosts) == agent.GHOST_LIMIT
    assert report.ghosts_total == 25
    assert "25" in report.ghosts_note
    # strongest first, so the half that survives truncation is the useful half
    assert report.ghosts[0]["similarity"] == 0.824
    assert [g["similarity"] for g in report.ghosts] == sorted(
        (g["similarity"] for g in report.ghosts), reverse=True
    )


def test_lint_reports_a_graph_that_no_longer_describes_this_bundle(project):
    # ids come from model-written titles, so a rebuild renames the tree and the
    # previous graph is about different concepts rather than merely out of date
    write_graph(
        project,
        {
            "nodes": [{"id": "revenue-recognition-policy"}],
            "ghosts": [
                {"source": "revenue-recognition-policy", "target": "price-list", "similarity": 0.9}
            ],
        },
    )
    note = agent.lint(cfg(project)).ghosts_note
    assert "out of date" in note
    assert "okragged index" in note


def test_a_graph_that_still_describes_the_bundle_is_not_called_out_of_date(project):
    ids = [c.concept_id for c in okf.read_concepts(project / "bundle")]
    write_graph(project, {"nodes": [{"id": cid} for cid in ids], "ghosts": []})
    assert agent.lint(cfg(project)).ghosts_note == ""


def test_a_broken_cross_link_is_a_warning_in_the_report_not_an_error(project):
    okf.write_concept(
        project / "bundle",
        okf.Concept(
            concept_id="pricing",
            type="Reference",
            title="Price List",
            description="What each plan costs.",
            body="Billed annually, see [Nothing](/nothing.md).",
        ),
    )
    report = agent.lint(cfg(project))
    assert report.errors == []
    assert any("W2" in warning for warning in report.warnings)


# --- the generated contract ------------------------------------------------------


def test_the_contract_tells_an_agent_to_read_the_index_before_whole_concepts(project):
    text = agent.render_agents_md(cfg(project))
    assert "Read the root index first" in text
    assert "read whole concept files" in text.lower()
    assert "Do not search for chunks" in text


def test_the_contract_names_the_index_as_a_resource_before_it_names_a_path(project):
    """This text is served as `okragged://contract` to a consumer holding no
    directory of ours, so step one cannot be an instruction to open a file. The
    path stays for a reader who does hold it - second, and marked as conditional."""
    text = agent.render_agents_md(cfg(project))
    step_one = next(line for line in text.splitlines() if line.startswith("1. "))

    assert step_one.index(agent.INDEX_URI) < step_one.index("bundle/index.md")
    assert "if you hold the directory" in step_one


def test_the_contract_lists_the_type_vocabulary_with_counts(project):
    text = agent.render_agents_md(cfg(project))
    assert "| `Policy` | 2 |" in text
    assert "| `Reference` | 2 |" in text


def test_the_contract_describes_the_bundles_directory_structure(project):
    okf.write_concept(
        project / "bundle",
        okf.Concept(concept_id="pricing/plans", type="Reference", title="Plans", body="x"),
    )
    text = agent.render_agents_md(cfg(project))
    assert "bundle/" in text and "index.md" in text
    assert "(4 concept(s) at the root)" in text
    assert "pricing/  (1 concept(s))" in text


def test_the_contract_says_no_tool_needs_a_credential(project):
    text = agent.render_agents_md(cfg(project))
    assert "**None of them needs a credential.**" in text
    assert "nothing here spends anybody's credential" in text
    for tool in ("search", "read_concept", "lint", "add_source"):
        assert f"`{tool}`" in text


def test_the_contract_says_a_recorded_source_is_not_yet_a_concept(project):
    """The half a client would otherwise get wrong: it records, and the concept
    arrives with a `build` nobody on this surface can trigger."""
    text = agent.render_agents_md(cfg(project))
    assert "records only" in text
    assert "okragged build" in text


def test_the_contract_no_longer_offers_a_way_to_re_ingest_the_corpus(project):
    """The tool is gone, so the contract must not describe one (§13 M5)."""
    text = agent.render_agents_md(cfg(project))
    assert "| `ingest` |" not in text
    assert "okragged build" in text


def test_the_generated_contract_names_no_path_in_the_producers_tree(project):
    """The M5 invariant, over the file as well as over the wire: `AGENTS.md` is
    served as `okragged://contract` to a consumer with none of these directories.

    Two kinds of path survive it, and both are addresses rather than instructions
    to open a file here: the bundle's own index, which is what `read_concept`
    reaches and what the served artifact is *made of*, and the producer's root in
    the registration snippet a foreign project pastes into its own config.
    """
    config = cfg(project)
    text = agent.render_agents_md(config)
    served_artifact = f"{config.bundle.root}/index.md"
    registration_target = project.resolve().as_posix()

    offenders = [
        found
        for found in producer_paths(text)
        if found != served_artifact and found != registration_target
    ]
    assert not offenders, offenders


def test_the_path_check_catches_the_operations_text_it_replaced():
    """A guard that always passes guards nothing (see `tests/test_encoding.py`).

    Both offenders are the real ones: M4's contract registered the server in a
    file only the producer has, and told a consumer to read its sidecar.
    """
    was = (
        "Three, over the MCP server registered in `.mcp.json`.\n"
        "- **ingest** — re-reads `sources/`, and only pays for the files whose bytes "
        "changed.\n"
        "- **lint** — Ghost edges come from `.okragged/graph.json`, so run `okragged "
        "index` if it reports having none to read."
    )
    assert producer_paths(was) == [".mcp.json", ".okragged/graph.json"]


def test_the_contract_explains_that_verified_is_never_written(project):
    text = agent.render_agents_md(cfg(project))
    assert "Never write a `verified` block" in text
    assert "human-reviewed" in text


def test_the_contract_regenerates_byte_identically_from_an_unchanged_bundle(project):
    first = agent.render_agents_md(cfg(project))
    assert agent.render_agents_md(cfg(project)) == first


def test_registering_the_server_leaves_the_other_mcp_servers_alone(project):
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "node"}}, "extra": 1}),
        encoding="utf-8",
        newline="\n",
    )
    agent.register(cfg(project))
    registered = json.loads((project / ".mcp.json").read_text(encoding="utf-8-sig"))

    assert registered["mcpServers"]["other"] == {"command": "node"}
    assert registered["extra"] == 1
    assert registered["mcpServers"]["okragged"]["args"] == ["mcp"]


def registered_entry(project) -> dict:
    agent.register(cfg(project))
    loaded = json.loads((project / ".mcp.json").read_text(encoding="utf-8-sig"))
    return loaded["mcpServers"]["okragged"]


def test_the_registration_entry_names_the_bare_console_script_when_path_resolves_it(
    project, monkeypatch
):
    """A venv path and a script path are true from one cwd and nowhere else, and
    `.mcp.json` is not gitignored — so a name that travels wins whenever it works."""
    monkeypatch.setattr(contract.shutil, "which", lambda name: f"/usr/bin/{name}")

    entry = registered_entry(project)

    assert entry == {"command": "okragged", "args": ["mcp"]}
    assert not producer_paths(json.dumps(entry))


def test_an_unresolvable_command_falls_back_to_the_script_beside_this_interpreter(
    project, monkeypatch
):
    """The failure this replaces was total and unreadable: a client spawns the
    server from `.mcp.json`, never from an activated shell, so `okragged` installed
    into a venv is not on the spawn's `PATH` and the whole server dies as
    `CONNECTION_CLOSED`. Same exposure as `okf/validate.py::_okflint`."""
    venv = project / "venv" / "Scripts"
    venv.mkdir(parents=True)
    beside = venv / ("okragged.exe" if os.name == "nt" else "okragged")
    beside.write_bytes(b"")
    monkeypatch.setattr(contract.shutil, "which", lambda name: None)
    monkeypatch.setattr(contract.sys, "executable", str(venv / "python.exe"))

    command = registered_entry(project)["command"]

    assert command == str(beside)
    assert Path(command).stem == "okragged", "the console script, not an interpreter"


def test_the_fallback_gives_up_rather_than_naming_a_script_that_is_not_there(
    project, monkeypatch
):
    """A guard that always fires guards nothing: an absolute path to a file that
    does not exist fails the spawn exactly as the bare name did, and reads worse."""
    monkeypatch.setattr(contract.shutil, "which", lambda name: None)
    monkeypatch.setattr(contract.sys, "executable", str(project / "nowhere" / "python.exe"))

    assert registered_entry(project)["command"] == "okragged"


def test_writing_the_contract_does_not_register_the_server(project):
    """The split: AGENTS.md is derived from the bundle and is rewritten whenever it
    changes; `.mcp.json` is the machine's MCP config, is the same bytes at every
    bundle size, and is written only when somebody asks."""
    written = agent.write_contract(cfg(project))

    assert [p.name for p in written] == ["AGENTS.md"]
    assert not (project / ".mcp.json").exists()


def test_a_project_reports_whether_its_server_is_registered(project):
    """What lets a build *mention* the registration without performing it."""
    config = cfg(project)
    assert not agent.is_registered(config)

    agent.register(config)
    assert agent.is_registered(config)


def test_registering_reports_nothing_registered_when_another_server_holds_the_file(
    project,
):
    """A `.mcp.json` that exists is not a `.mcp.json` that registers us."""
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "node"}}}),
        encoding="utf-8",
        newline="\n",
    )
    assert not agent.is_registered(cfg(project))


def test_the_contract_hands_a_foreign_project_an_entry_that_names_the_root_absolutely(
    project,
):
    """A consumer's cwd is its own tree, so the target has to be spelled out."""
    text = agent.render_agents_md(cfg(project))
    assert '"command": "okragged"' in text
    assert '"--project"' in text
    assert project.resolve().as_posix() in text


def test_the_contract_points_at_the_resources_rather_than_at_a_file_to_open(project):
    text = agent.render_agents_md(cfg(project))
    assert agent.CONTRACT_URI in text
    assert agent.INDEX_URI in text


def test_the_contract_lands_beside_the_bundle_never_inside_it(project):
    written = agent.write_contract(cfg(project))
    assert [p.name for p in written] == ["AGENTS.md"]
    assert (project / "agent" / "AGENTS.md").exists()
    assert not list((project / "bundle").rglob("AGENTS.md"))


def test_nothing_the_agent_layer_does_writes_into_the_bundle(project):
    before = bundle_bytes(project)
    agent.search(cfg(project), "revenue recognition")
    agent.read_concept(cfg(project), "revenue-policy")
    agent.lint(cfg(project))
    agent.write_contract(cfg(project))
    assert bundle_bytes(project) == before, "nothing derived may enter bundle/"


# --- okragged agent, and what --register adds ------------------------------------


def run_cli(project: Path, monkeypatch, args: list[str]):
    from typer.testing import CliRunner

    from okragged import cli

    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    return result


def test_the_agent_command_refreshes_the_contract_without_registering(project, monkeypatch):
    result = run_cli(project, monkeypatch, ["agent"])

    assert (project / "agent" / "AGENTS.md").exists()
    assert not (project / ".mcp.json").exists()
    assert "--register" in result.output, "an unregistered project should be told how"


def test_the_agent_command_registers_only_when_asked(project, monkeypatch):
    run_cli(project, monkeypatch, ["agent", "--register"])

    entry = json.loads((project / ".mcp.json").read_text(encoding="utf-8-sig"))
    entry = entry["mcpServers"]["okragged"]
    assert entry["args"] == ["mcp"]
    # bare on PATH or the console script beside this interpreter; never an
    # interpreter and never a script path (see `contract._command`)
    assert Path(entry["command"]).stem == "okragged"


def test_the_agent_command_stops_offering_to_register_once_it_has(project, monkeypatch):
    run_cli(project, monkeypatch, ["agent", "--register"])
    result = run_cli(project, monkeypatch, ["agent"])
    assert "--register" not in result.output


def test_registering_never_truncates_a_config_holding_other_servers(project, monkeypatch):
    """`.mcp.json` is read, merged and written back, and it is the one file here
    that carries other people's data - so the write is temp-file-plus-rename."""
    original = json.dumps({"mcpServers": {"other": {"command": "node"}}})
    (project / ".mcp.json").write_text(original, encoding="utf-8", newline="\n")

    monkeypatch.setattr(
        agent.contract.os, "replace", _explode("the rename failed mid-publish")
    )
    with pytest.raises(RuntimeError):
        agent.register(cfg(project))

    assert (project / ".mcp.json").read_text(encoding="utf-8-sig") == original
    assert not list(project.glob("*.tmp")), "the temp file must not survive"


def _explode(message: str):
    def boom(*_args, **_kwargs):
        raise RuntimeError(message)

    return boom


# --- ingest ----------------------------------------------------------------------


def fake_synthesize(**kwargs) -> SynthResult:
    title = kwargs["source_title"].title()
    return SynthResult(
        type="Policy",
        title=title,
        description=f"About {title}.",
        body_markdown=f"# {title}\n\nText.[^{kwargs['source_id']}]\n\n[^{kwargs['source_id']}]: x",
        tags=[],
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
    )


@pytest.fixture
def ingestable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline, "weave", lambda **kw: WeaveResult(kw["body"], invented=[]))
    (tmp_path / "okragged.toml").write_text(TOML, encoding="utf-8", newline="\n")
    (tmp_path / "sources").mkdir()
    for name in ("alpha", "beta"):
        (tmp_path / "sources" / f"{name}.md").write_text(
            f"# {name}\n\nA short note about {name}.\n", encoding="utf-8", newline="\n"
        )
    return tmp_path


def test_an_ingest_regenerates_the_agent_contract(ingestable):
    pipeline.ingest(config_module.load(ingestable))
    text = (ingestable / "agent" / "AGENTS.md").read_text(encoding="utf-8-sig")
    assert "2 concept(s)" in text


def test_an_ingest_never_registers_the_server(ingestable):
    """`.mcp.json` is not derived from the bundle - the entry is the same bytes at
    every bundle size - and it belongs to whatever agent works in this directory.
    A build that merged itself into it would be editing somebody else's config."""
    pipeline.ingest(config_module.load(ingestable))
    assert not (ingestable / ".mcp.json").exists()


def test_ingesting_one_path_leaves_the_sources_it_did_not_look_at_alone(ingestable):
    config = config_module.load(ingestable)
    pipeline.ingest(config)

    summary = pipeline.ingest(config, only=["sources/alpha.md"])
    assert [o.uri for o in summary.outcomes] == ["sources/alpha.md"]
    # the other source was never discovered, which is not evidence that it is gone
    assert summary.deprecated == []
    assert okf.parse(
        (ingestable / "bundle" / "beta.md").read_text(encoding="utf-8-sig"), "beta"
    ).status == "draft"


def test_a_path_outside_the_project_is_refused_rather_than_ingested(ingestable):
    with pytest.raises(ValueError, match="outside the project"):
        pipeline.ingest(config_module.load(ingestable), only=["../elsewhere.md"])


# --- add_source ------------------------------------------------------------------

ASSERTION = "Invoices and refunds share a customer id, which no document says."


def added(project: Path, text: str = ASSERTION, title: str | None = None):
    from okragged.agent.write import add_source

    return add_source(config_module.load(project), text, title)


def calls(monkeypatch) -> list[str]:
    """Every synthesis this run pays for, so a skip can be told from a rewrite."""
    seen: list[str] = []

    def counted(**kwargs):
        seen.append(kwargs["source_uri"])
        return fake_synthesize(**kwargs)

    monkeypatch.setattr(pipeline, "synthesize", counted)
    return seen


def test_add_source_writes_the_assertion_into_the_corpus_as_a_real_source(ingestable):
    result = added(ingestable)
    path = ingestable / result.uri

    assert result.uri.startswith("sources/conversations/")
    assert path.read_text(encoding="utf-8-sig") == ASSERTION + "\n"
    assert result.recorded


def test_add_source_records_without_calling_a_model(ingestable, monkeypatch):
    """The security boundary, and why all four tools are credential-free.

    Synthesis costs money, and stdio MCP has no identity — the server is a
    subprocess of whatever client spawned it — so "whose key pays" would be
    settled by environment precedence, with the producer's `.env` funding a
    stranger's writes. Not calling a model removes the question.
    """
    seen = calls(monkeypatch)
    result = added(ingestable)

    assert seen == [], "nothing on this surface may spend a credential"
    assert (ingestable / result.uri).is_file()
    assert not (ingestable / "bundle").exists(), "the concept waits for a build"


def test_add_source_needs_no_credential_at_all(ingestable, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    result = added(ingestable)
    assert (ingestable / result.uri).is_file()


def test_the_next_build_authors_the_concept_the_added_source_describes(
    ingestable, monkeypatch
):
    """Recording and synthesizing are split across a trust boundary, not dropped:
    the operator's own `build`, with the operator's own key, finishes the job."""
    result = added(ingestable)
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    summary = pipeline.ingest(config_module.load(ingestable))

    produced = next(o for o in summary.outcomes if o.uri == result.uri)
    assert produced.concept_id and not produced.error
    concept = okf.parse(
        (ingestable / "bundle" / f"{produced.concept_id}.md").read_text(encoding="utf-8-sig"),
        produced.concept_id,
    )
    assert result.uri in concept.sources[0].resource
    assert concept.extra["x_okragged_source_hash"]


def test_adding_a_source_leaves_the_rest_of_the_corpus_alone_and_undeprecated(ingestable):
    """A partial run suppresses the vanished sweep (§6.1), which is the whole
    reason the tool ingests one path rather than sweeping."""
    pipeline.ingest(config_module.load(ingestable))
    before = {
        name: (ingestable / "bundle" / f"{name}.md").read_text(encoding="utf-8-sig")
        for name in ("alpha", "beta")
    }
    added(ingestable)

    for name, text in before.items():
        after = okf.parse((ingestable / "bundle" / f"{name}.md").read_text(encoding="utf-8-sig"), name)
        assert after.status == "draft"
        assert (ingestable / "bundle" / f"{name}.md").read_text(encoding="utf-8-sig") == text


def test_re_adding_the_same_assertion_writes_nothing_a_second_time(ingestable):
    first = added(ingestable)
    again = added(ingestable)

    assert again.uri == first.uri
    assert first.recorded and not again.recorded


def test_add_source_generates_its_path_and_a_title_cannot_escape_the_directory(ingestable):
    result = added(ingestable, title="../../etc/passwd")
    written = ingestable / result.uri

    assert written.parent == ingestable / "sources" / "conversations"
    assert written.is_file()
    assert not list(ingestable.parent.glob("passwd*"))


def test_add_source_refuses_text_that_is_already_a_concept(ingestable):
    """The `add_concept` misuse the design refuses, caught where it is cheap."""
    with pytest.raises(ValueError, match="prose, not a concept"):
        added(ingestable, text="---\ntype: Policy\ntitle: Refunds\n---\n\nRefunds happen.\n")
    assert not (ingestable / "sources" / "conversations").exists()


def test_add_source_refuses_when_the_configured_globs_would_never_discover_it(ingestable):
    """`include` is configurable, so the file could land where nothing reads it."""
    (ingestable / "okragged.toml").write_text(
        TOML.replace('include = ["**/*.md"]', 'include = ["*.md"]'),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="include"):
        added(ingestable)
    assert not list((ingestable / "sources" / "conversations").glob("*.md"))


def test_add_source_takes_no_lock_and_is_not_refused_by_a_writer_holding_one(
    ingestable,
):
    """It writes one file into `sources/`, which okragged never reads back as
    state: the path is generated per assertion and identical bytes reuse it, so
    there is nothing for a concurrent build to collide with. Taking the lock would
    make recording an assertion fail for the duration of somebody's rebuild."""
    from okragged import lock

    config = config_module.load(ingestable)
    with lock.hold(config.sidecar_root, "some other build"):
        result = added(ingestable)

    assert (ingestable / result.uri).is_file()


# --- the server ------------------------------------------------------------------

def server_for(project: Path):
    from okragged.agent.server import build_server

    return build_server(cfg(project))


def test_the_server_exposes_exactly_the_four_tools_the_contract_names(project):
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    import anyio
    from mcp import Client

    async def go():
        async with Client(server_for(project)) as client:
            return sorted(tool.name for tool in (await client.list_tools()).tools)

    assert anyio.run(go) == ["add_source", "lint", "read_concept", "search"]


def test_no_tool_on_the_surface_can_sweep_the_corpus(project):
    """A recipient holding the bundle *and* the sidecar has a ledger and no
    `sources/`, so a full `ingest` would read every source as vanished and mark
    every concept `deprecated`. Removing the tool closes that structurally, which
    is the difference between unreachable and merely guarded (§13 M5)."""
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    import anyio
    from mcp import Client

    async def go():
        async with Client(server_for(project)) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    assert "ingest" not in anyio.run(go)


def test_the_tool_descriptions_carry_the_rules_a_client_may_never_read_elsewhere(project):
    """`instructions` are client-dependent and resources are an optional client
    capability; a tool is shown with its description wherever tools are shown."""
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    import anyio
    from mcp import Client

    async def go():
        async with Client(server_for(project)) as client:
            return {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}

    described = anyio.run(go)
    assert agent.INDEX_URI in described["search"]
    assert "never bodies" in described["search"]
    assert "whole file" in described["read_concept"]
    assert "`verified`" in described["add_source"]
    assert "credential" in described["add_source"]


def test_a_session_with_no_credential_searches_reads_and_lints_over_the_protocol(project):
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    import anyio
    from mcp import Client

    async def go():
        async with Client(server_for(project)) as client:
            found = await client.call_tool("search", {"query": "revenue recognition", "k": 3})
            read = await client.call_tool(
                "read_concept", {"concept_id": found.structured_content["results"][0]["concept_id"]}
            )
            report = await client.call_tool("lint", {})
            return found, read, report

    found, read, report = anyio.run(go)
    assert found.structured_content["path"] == agent.TRAVERSAL
    assert found.structured_content["results"][0]["concept_id"] == "revenue-policy"
    assert "Revenue is recognized on delivery" in read.content[0].text
    assert report.structured_content["concepts"] == 4


def test_a_whole_session_with_no_credential_can_also_write(project, monkeypatch):
    """The M5 exit criterion's shape: a consumer holding somebody else's bundle and
    no key of their own drives every tool, `add_source` included. Recording an
    assertion must not be the one thing that demands a credential over a protocol
    that has nobody to charge."""
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    import anyio
    from mcp import Client

    async def go():
        async with Client(server_for(project)) as client:
            wrote = await client.call_tool("add_source", {"text": "Two tables are related."})
            return wrote, await client.call_tool("lint", {})

    wrote, report = anyio.run(go)
    assert not wrote.is_error, wrote.content[0].text
    assert wrote.structured_content["uri"].startswith("sources/conversations/")
    assert (project / wrote.structured_content["uri"]).is_file()
    assert report.structured_content["concepts"] == 4


# --- the contract, over the protocol ---------------------------------------------


def resources_of(project: Path):
    import anyio
    from mcp import Client

    async def go():
        async with Client(server_for(project)) as client:
            listed = (await client.list_resources()).resources
            contract = await client.read_resource(agent.CONTRACT_URI)
            index = await client.read_resource(agent.INDEX_URI)
            return listed, contract.contents[0].text, index.contents[0].text

    return anyio.run(go)


def test_the_server_exports_the_contract_and_the_index_as_resources(project):
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    listed, _, _ = resources_of(project)
    assert sorted(str(resource.uri) for resource in listed) == [
        agent.CONTRACT_URI,
        agent.INDEX_URI,
    ]


def test_the_contract_served_over_the_wire_is_the_file_on_disk(project):
    """One renderer, so a consumer and a reader of `agent/` cannot be told
    different things about one bundle."""
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    agent.write_contract(cfg(project))
    _, contract, index = resources_of(project)

    assert contract == (project / "agent" / "AGENTS.md").read_text(encoding="utf-8-sig")
    assert index == agent.read_concept(cfg(project), "index")


def test_nothing_the_server_says_at_the_handshake_names_a_path_in_the_producers_tree(
    project,
):
    """The M5 invariant: layer 3 crosses the process boundary over the protocol,
    never over the filesystem. A consumer registers this server from its own
    checkout, so a path relative to this root names a file it does not have."""
    pytest.importorskip("mcp", reason='the MCP SDK is an optional extra: pip install -e ".[mcp]"')
    import anyio
    from mcp import Client

    async def go():
        async with Client(server_for(project)) as client:
            return (
                client.instructions or "",
                (await client.list_tools()).tools,
                (await client.list_resources()).resources,
            )

    instructions, tools, resources = anyio.run(go)
    said = [instructions]
    said += [tool.description or "" for tool in tools]
    said += [
        f"{resource.uri} {resource.name} {resource.title or ''} {resource.description or ''}"
        for resource in resources
    ]

    offenders = {text: found for text in said if (found := producer_paths(text))}
    assert not offenders, offenders


def test_the_path_check_catches_the_instruction_and_the_entry_it_replaced():
    """A guard that always passes guards nothing (see `tests/test_encoding.py`).

    Both offenders are the real ones: M4's handshake told every client that
    `agent/AGENTS.md` was the contract, and M4's registration entry spawned the
    server through the producer's own venv.
    """
    from okragged.agent.server import INSTRUCTIONS

    was = (
        "An Open Knowledge Format bundle. Read index.md first, then read whole concept "
        "files — `search` returns pointers, not bodies. agent/AGENTS.md is the contract."
    )
    entry = json.dumps({"command": ".venv/Scripts/python.exe", "args": ["agent/mcp_server.py"]})

    assert producer_paths(was) == ["agent/AGENTS.md"]
    assert producer_paths(entry) == [".venv/Scripts/python.exe", "agent/mcp_server.py"]
    assert producer_paths(INSTRUCTIONS) == []


# --- okragged mcp ----------------------------------------------------------------


def served_root(project: Path, monkeypatch, args: list[str]) -> Path:
    """The root `okragged mcp` would have served, without serving it."""
    from typer.testing import CliRunner

    from okragged import cli
    from okragged.agent import server

    seen: list[Path] = []
    monkeypatch.setattr(server, "main", seen.append)
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    return seen[0]


def test_the_server_command_serves_the_project_it_is_pointed_at(project, monkeypatch, tmp_path):
    """A launcher that inferred its bundle from `__file__` could not be installed."""
    elsewhere = tmp_path / "consumer"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert served_root(project, monkeypatch, ["mcp", "--project", str(project)]) == project


def test_the_server_command_takes_its_project_from_the_environment_too(
    project, monkeypatch, tmp_path
):
    elsewhere = tmp_path / "consumer"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("OKRAGGED_PROJECT", str(project))
    assert served_root(project, monkeypatch, ["mcp"]) == project


def test_the_server_command_says_so_on_stderr_when_there_is_no_bundle_to_serve(
    tmp_path, monkeypatch
):
    """stdout carries the protocol, so a diagnostic there corrupts the session."""
    from typer.testing import CliRunner

    from okragged import cli

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["mcp"])
    assert result.exit_code == 2
    assert "no bundle" in result.stderr
    assert result.stdout == ""
