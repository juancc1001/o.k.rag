from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from okragged import config as config_module
from okragged import okf, vectors
from okragged.vectors import EmbedError, graph as graph_module
from okragged.vectors.providers import fastembed, gemini, resolve

TOML = """\
[bundle]
root = "bundle"
actor = "okragged/test"

[vectors]
provider = "gemini"
dim = 3
knn_k = 8
ghost_threshold = 0.82

[projection]
method = "pca"
random_state = 42
"""

# A three-dimensional toy space. A marker in the concept body picks a direction,
# so similarity between two concepts is something the test states rather than
# something a real model decides.
DIRECTIONS = {
    "mark-north": [1.0, 0.0, 0.0],
    "mark-nne": [0.99, 0.14, 0.0],
    "mark-nnne": [0.999, 0.045, 0.0],
    "mark-east": [0.0, 1.0, 0.0],
}


def fake_embed(chunks, *, model, dim):
    return [_direction(text) for text in chunks]


def _direction(text: str) -> list[float]:
    for marker, vector in DIRECTIONS.items():
        if marker in text:
            return vector
    return [0.0, 0.0, 1.0]


def cosine(a: str, b: str) -> float:
    left = np.array(DIRECTIONS[a])
    right = np.array(DIRECTIONS[b])
    return float(left @ right / (np.linalg.norm(left) * np.linalg.norm(right)))


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(gemini, "embed", fake_embed)
    (tmp_path / "okragged.toml").write_text(TOML, encoding="utf-8", newline="\n")
    (tmp_path / "bundle").mkdir()
    return tmp_path


def write(project: Path, concept_id: str, body: str, **fields) -> None:
    okf.write_concept(
        project / "bundle",
        okf.Concept(
            concept_id=concept_id,
            type=fields.pop("type", "Concept"),
            title=fields.pop("title", concept_id),
            description=fields.pop("description", "d"),
            body=body,
            **fields,
        ),
    )


def run(project: Path) -> vectors.IndexSummary:
    return vectors.index(config_module.load(project))


def graph_of(project: Path) -> dict:
    return json.loads((project / ".okragged" / "graph.json").read_text(encoding="utf-8-sig"))


# --- the provider interface ------------------------------------------------------


def test_both_embedding_providers_are_registered():
    assert set(vectors.PROVIDERS) == {"gemini", "fastembed"}
    assert resolve("gemini") is gemini
    assert resolve("fastembed") is fastembed


def test_an_unknown_embedding_provider_names_the_known_ones():
    with pytest.raises(ValueError, match="fastembed, gemini"):
        resolve("openai")


def test_embedding_providers_expose_the_interface_the_index_relies_on():
    for impl in (gemini, fastembed):
        assert isinstance(impl.NAME, str)
        assert isinstance(impl.DEFAULT_MODEL, str)
        assert isinstance(impl.WINDOW_CHARS, int)
        assert impl.ENV_VARS is not None
        for attr in ("embed", "credential_present"):
            assert callable(getattr(impl, attr)), f"{impl.NAME}.{attr}"


def test_the_local_provider_reports_its_optional_extra_rather_than_an_import_error(
    monkeypatch,
):
    monkeypatch.setattr(fastembed, "_MODELS", {})
    monkeypatch.setitem(sys.modules, "fastembed", None)  # as if it were not installed
    with pytest.raises(EmbedError, match="optional extra"):
        fastembed.embed(["x"], model=fastembed.DEFAULT_MODEL, dim=384)


# --- embed -----------------------------------------------------------------------


def test_one_vector_per_concept_covers_title_description_and_body(project_dir, monkeypatch):
    seen: list[str] = []

    def recording(chunks, *, model, dim):
        seen.extend(chunks)
        return fake_embed(chunks, model=model, dim=dim)

    monkeypatch.setattr(gemini, "embed", recording)
    write(project_dir, "north", "mark-north body text.", title="North", description="Points up.")
    summary = run(project_dir)

    assert summary.embed.embedded == ["north"]
    assert "North" in seen[0] and "Points up." in seen[0] and "mark-north" in seen[0]
    assert np.load(project_dir / ".okragged" / "embeddings.npy").shape == (1, 3)


def test_a_concept_whose_body_is_unchanged_is_not_re_embedded(project_dir, monkeypatch):
    calls: list[int] = []

    def counting(chunks, *, model, dim):
        calls.append(len(chunks))
        return fake_embed(chunks, model=model, dim=dim)

    monkeypatch.setattr(gemini, "embed", counting)
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "east", "mark-east body text.")
    run(project_dir)
    assert len(calls) == 2

    second = run(project_dir)
    assert len(calls) == 2  # no further model call
    assert sorted(second.embed.reused) == ["east", "north"]
    assert second.embed.embedded == []


def test_an_edited_body_is_re_embedded_while_the_others_are_reused(project_dir):
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "east", "mark-east body text.")
    run(project_dir)

    write(project_dir, "east", "mark-east body text, revised.")
    summary = run(project_dir)
    assert summary.embed.embedded == ["east"]
    assert summary.embed.reused == ["north"]


def test_a_concept_that_fails_to_embed_does_not_lose_the_others(project_dir, monkeypatch):
    def fail_the_east(chunks, *, model, dim):
        if any("mark-east" in text for text in chunks):
            raise EmbedError("gemini HTTP 429: quota")
        return fake_embed(chunks, model=model, dim=dim)

    monkeypatch.setattr(gemini, "embed", fail_the_east)
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "east", "mark-east body text.")
    summary = run(project_dir)

    assert summary.embed.embedded == ["north"]
    assert "429" in summary.embed.errors["east"]
    # the concept is still a node; only its position is missing
    assert [n["id"] for n in graph_of(project_dir)["nodes"]] == ["east", "north"]


def test_a_concept_longer_than_the_window_is_mean_pooled_into_one_vector(
    project_dir, monkeypatch
):
    widths: list[int] = []

    def recording(chunks, *, model, dim):
        widths.append(len(chunks))
        return fake_embed(chunks, model=model, dim=dim)

    monkeypatch.setattr(gemini, "WINDOW_CHARS", 60)
    monkeypatch.setattr(gemini, "embed", recording)
    write(project_dir, "north", "mark-north " + "palabra " * 60)
    run(project_dir)

    assert widths[0] > 1, "a body past the window must be chunked"
    # only the concept vector is kept — chunk vectors are never stored
    assert np.load(project_dir / ".okragged" / "embeddings.npy").shape == (1, 3)
    meta = json.loads(
        (project_dir / ".okragged" / "embeddings_meta.json").read_text(encoding="utf-8-sig")
    )
    assert [row["concept_id"] for row in meta["rows"]] == ["north"]


def test_stored_vectors_are_l2_normalized(project_dir, monkeypatch):
    monkeypatch.setattr(gemini, "embed", lambda chunks, **_: [[3.0, 4.0, 0.0] for _ in chunks])
    write(project_dir, "north", "mark-north body text.")
    run(project_dir)
    matrix = np.load(project_dir / ".okragged" / "embeddings.npy")
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0)


def test_changing_the_model_re_embeds_rather_than_mixing_dimensions(project_dir, monkeypatch):
    write(project_dir, "north", "mark-north body text.")
    run(project_dir)

    monkeypatch.setattr(gemini, "embed", lambda chunks, **_: [[1.0, 0.0, 0.0, 0.0] for _ in chunks])
    (project_dir / "okragged.toml").write_text(
        TOML.replace('provider = "gemini"', 'provider = "gemini"\nmodel = "other-model"'),
        encoding="utf-8",
        newline="\n",
    )
    summary = run(project_dir)
    assert summary.embed.embedded == ["north"]
    assert np.load(project_dir / ".okragged" / "embeddings.npy").shape == (1, 4)


def test_a_vector_whose_concept_is_gone_is_dropped(project_dir):
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "east", "mark-east body text.")
    run(project_dir)

    (project_dir / "bundle" / "east.md").unlink()
    summary = run(project_dir)
    assert summary.embed.dropped == ["east"]
    assert np.load(project_dir / ".okragged" / "embeddings.npy").shape == (1, 3)


def test_the_index_writes_nothing_into_the_bundle(project_dir):
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "east", "mark-east body text.")
    before = {p: p.read_bytes() for p in sorted((project_dir / "bundle").rglob("*"))}
    run(project_dir)
    after = {p: p.read_bytes() for p in sorted((project_dir / "bundle").rglob("*"))}

    assert before == after, "nothing derived may enter bundle/"
    assert "embedding" not in (project_dir / "bundle" / "north.md").read_text(
        encoding="utf-8-sig"
    )


def test_chunking_splits_on_whitespace_and_covers_the_text():
    text = "una frase " * 40
    pieces = vectors.chunk(text, 50)
    assert len(pieces) > 1
    assert all(len(p) <= 50 for p in pieces)
    assert " ".join(pieces).split() == text.split()


def test_an_unbroken_token_longer_than_the_window_is_still_chunked():
    pieces = vectors.chunk("x" * 25, 10)
    assert pieces == ["x" * 10, "x" * 10, "x" * 5]


# --- project ---------------------------------------------------------------------


def test_the_projection_falls_back_to_pca_below_fifteen_concepts():
    matrix = np.eye(5, 8, dtype=np.float32)
    coords, method = vectors.project(matrix, method="umap", random_state=42)
    assert method == "pca"
    assert coords.shape == (5, 2)


def test_the_same_embeddings_project_to_the_same_layout():
    rng = np.random.default_rng(0)
    matrix = rng.normal(size=(10, 8)).astype(np.float32)
    first, _ = vectors.project(matrix, method="pca", random_state=42)
    second, _ = vectors.project(matrix, method="pca", random_state=42)
    assert np.array_equal(first, second)


def test_a_single_concept_still_gets_two_coordinates():
    coords, _ = vectors.project(np.ones((1, 4), np.float32), method="pca")
    assert coords.shape == (1, 2)


def test_the_layout_records_which_projection_ran(project_dir):
    write(project_dir, "north", "mark-north body text.")
    summary = run(project_dir)
    layout = json.loads(
        (project_dir / ".okragged" / "layout.json").read_text(encoding="utf-8-sig")
    )
    assert layout["method"] == summary.method == "pca"
    assert list(layout["coords"]) == ["north"]


# --- graph -----------------------------------------------------------------------


def test_a_similar_unlinked_pair_becomes_a_ghost_edge(project_dir):
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "nne", "mark-nne body text.")
    run(project_dir)

    ghosts = graph_of(project_dir)["ghosts"]
    assert [(g["source"], g["target"], g["kind"]) for g in ghosts] == [
        ("nne", "north", "semantic")
    ]
    assert ghosts[0]["similarity"] == pytest.approx(cosine("mark-north", "mark-nne"), abs=1e-3)


def test_a_linked_pair_is_never_a_ghost_edge(project_dir):
    write(project_dir, "north", "mark-north body, see [NNE](/nne.md).")
    write(project_dir, "nne", "mark-nne body text.")
    run(project_dir)

    graph = graph_of(project_dir)
    assert graph["ghosts"] == []
    assert graph["links"] == [{"source": "north", "target": "nne", "kind": "explicit"}]


def test_a_link_in_either_direction_suppresses_the_ghost(project_dir):
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "nne", "mark-nne body, see [North](/north.md).")
    run(project_dir)
    assert graph_of(project_dir)["ghosts"] == []


def test_a_pair_below_the_threshold_is_not_a_ghost(project_dir):
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "east", "mark-east body text.")
    run(project_dir)
    assert graph_of(project_dir)["ghosts"] == []


def test_the_ghost_threshold_comes_from_config(project_dir):
    (project_dir / "okragged.toml").write_text(
        TOML.replace("ghost_threshold = 0.82", "ghost_threshold = 0.0"),
        encoding="utf-8",
        newline="\n",
    )
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "east", "mark-east body text.")
    run(project_dir)
    assert len(graph_of(project_dir)["ghosts"]) == 1


def test_a_similar_pair_outside_the_top_k_is_not_a_ghost(project_dir):
    (project_dir / "okragged.toml").write_text(
        TOML.replace("knn_k = 8", "knn_k = 1"), encoding="utf-8", newline="\n"
    )
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "nne", "mark-nne body text.")
    write(project_dir, "nnne", "mark-nnne body text.")
    run(project_dir)

    pairs = {(g["source"], g["target"]) for g in graph_of(project_dir)["ghosts"]}
    # nnne is the nearest neighbour of both others, so north/nne never make the cut
    assert pairs == {("nnne", "north"), ("nne", "nnne")}


def test_a_ghost_pair_is_reported_once_not_from_both_ends(project_dir):
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "nne", "mark-nne body text.")
    write(project_dir, "nnne", "mark-nnne body text.")
    run(project_dir)

    ghosts = graph_of(project_dir)["ghosts"]
    pairs = [(g["source"], g["target"]) for g in ghosts]
    assert len(pairs) == len(set(pairs)) == 3
    assert all(source < target for source, target in pairs)


def test_node_degree_counts_the_links_that_touch_it(project_dir):
    write(project_dir, "north", "mark-north, see [NNE](/nne.md) and [East](/east.md).")
    write(project_dir, "nne", "mark-nne body text.")
    write(project_dir, "east", "mark-east body text.")
    run(project_dir)

    degree = {n["id"]: n["degree"] for n in graph_of(project_dir)["nodes"]}
    assert degree == {"east": 1, "nne": 1, "north": 2}


def test_a_broken_link_named_twice_is_counted_once(project_dir):
    write(project_dir, "north", "mark-north. See [x](/nope.md) and again [x](/nope.md).")
    run(project_dir)
    stats = graph_of(project_dir)["stats"]
    # one body, one missing target, one broken link — the same way naming a real
    # target twice makes one edge
    assert stats["broken_links"] == 1


def test_two_concepts_pointing_at_the_same_missing_target_are_two_broken_links(project_dir):
    write(project_dir, "north", "mark-north. See [x](/nope.md).")
    write(project_dir, "east", "mark-east. See [x](/nope.md).")
    run(project_dir)
    assert graph_of(project_dir)["stats"]["broken_links"] == 2


def test_the_stats_count_concepts_links_ghosts_and_broken_links(project_dir):
    write(project_dir, "north", "mark-north, see [NNE](/nne.md) and [Nobody](/nobody.md).")
    write(project_dir, "nne", "mark-nne body text.")
    write(project_dir, "east", "mark-east body text.")
    run(project_dir)

    assert graph_of(project_dir)["stats"] == {
        "concepts": 3,
        "links": 1,
        "ghosts": 0,
        "broken_links": 1,
    }


def test_trust_is_derived_from_verified_and_never_written_back(project_dir):
    write(project_dir, "north", "mark-north body text.")
    write(project_dir, "nne", "mark-nne body text.", verified=[{"by": "human:jc"}])
    write(project_dir, "east", "mark-east body text.", verified=[{"by": "agent:okragged"}])
    run(project_dir)

    trust = {n["id"]: n["trust"] for n in graph_of(project_dir)["nodes"]}
    assert trust == {"north": "unverified", "nne": "human-reviewed", "east": "machine-confirmed"}
    assert "verified" not in (project_dir / "bundle" / "north.md").read_text(
        encoding="utf-8-sig"
    )


def test_a_stale_or_deprecated_concept_is_marked_on_its_node(project_dir):
    write(project_dir, "north", "mark-north body text.", stale_after="2000-01-01")
    write(project_dir, "east", "mark-east body text.", status="deprecated")
    run(project_dir)

    nodes = {n["id"]: n for n in graph_of(project_dir)["nodes"]}
    assert nodes["north"]["stale"] is True
    assert nodes["east"]["stale"] is False
    assert nodes["east"]["status"] == "deprecated"


def test_a_graph_node_carries_the_concepts_own_title_and_type(project_dir):
    write(project_dir, "north", "mark-north body.", title="Norte Magnético", type="Decision")
    run(project_dir)
    node = graph_of(project_dir)["nodes"][0]
    assert node["title"] == "Norte Magnético" and node["type"] == "Decision"


def test_an_empty_bundle_still_produces_a_readable_graph(project_dir):
    summary = run(project_dir)
    assert summary.stats == {"concepts": 0, "links": 0, "ghosts": 0, "broken_links": 0}
    assert graph_of(project_dir)["nodes"] == []


def test_everything_the_index_writes_lands_in_the_sidecar(project_dir):
    write(project_dir, "north", "mark-north body text.")
    summary = run(project_dir)
    assert {p.name for p in summary.written} == {
        "embeddings.npy",
        "embeddings_meta.json",
        "layout.json",
        "graph.json",
    }
    assert all(p.parent == project_dir / ".okragged" for p in summary.written)


def test_the_trust_tiers_are_read_off_the_concept_not_guessed():
    concept = okf.Concept(concept_id="a", type="Concept", body="b")
    assert graph_module.trust(concept) == "unverified"
    concept.verified = [{"by": "agent:x"}, {"by": "human:jc"}]
    assert graph_module.trust(concept) == "human-reviewed"
