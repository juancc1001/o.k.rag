"""M2 item 4: an index.md for every directory, and the three §13 defects.

Index generation reads the bundle off disk and needs no model, so most of this
is a plain filesystem test.
"""

from __future__ import annotations

import pytest

from ragged import config as config_module
from ragged import okf, pipeline

from test_pipeline import (  # noqa: F401  (project/segmented are used as fixtures)
    SAMPLE,
    fake_synthesize,
    project,
    segmented,
)


def concept(concept_id: str, title: str, description: str = "d", **extra) -> okf.Concept:
    return okf.Concept(
        concept_id=concept_id,
        type="Document",
        title=title,
        description=description,
        body="Body.",
        **extra,
    )


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    okf.write_concept(root, concept("delitos", "Delitos Informáticos en Argentina", "Marco legal."))
    okf.write_concept(root, concept("delitos/tipos", "Tipos de Delito", "Las figuras penales."))
    okf.write_concept(root, concept("delitos/penas", "Penas Aplicables", "Las escalas."))
    okf.write_concept(root, concept("suelto/nota", "Una Nota Suelta", "Sin concepto padre."))
    return root


# --- defect 1: labels came from the slug -----------------------------------------


def test_an_index_entry_uses_the_concept_title_not_its_slug(bundle):
    okf.write_indexes(bundle, "0.2", "T")
    index = (bundle / "index.md").read_text(encoding="utf-8-sig")
    # the slug already folded the accent away; title-casing it then read as English
    assert "[Delitos Informáticos en Argentina](./delitos.md)" in index
    assert "Delitos Informaticos" not in index


def test_an_entry_carries_the_target_description_verbatim(bundle):
    okf.write_indexes(bundle, "0.2", "T")
    nested = (bundle / "delitos" / "index.md").read_text(encoding="utf-8-sig")
    assert "[Tipos de Delito](./tipos.md) - Las figuras penales." in nested


# --- defect 2: the bundle title named the tool -----------------------------------


def test_the_bundle_title_comes_from_config_when_it_is_set(project, monkeypatch):
    (project / "ragged.toml").write_text(
        '[bundle]\nroot = "bundle"\ntitle = "Derecho Informático"\nactor = "ragged/test"\n\n'
        '[sources.local]\npath = "sources"\ninclude = ["**/*.md"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    assert "# Derecho Informático" in (cfg.bundle_root / "index.md").read_text(
        encoding="utf-8-sig"
    )


def test_without_a_title_the_directory_name_is_still_the_fallback(tmp_path):
    cfg = config_module.load(tmp_path)
    assert cfg.bundle_title == tmp_path.name.replace("-", " ").replace("_", " ").title()


def test_the_okflint_manifest_and_the_root_index_agree_on_the_name(project, monkeypatch):
    (project / "ragged.toml").write_text(
        '[bundle]\nroot = "bundle"\ntitle = "Derecho Informático"\n\n'
        '[sources.local]\npath = "sources"\ninclude = ["**/*.md"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)
    manifest = (project / okf.MANIFEST_NAME).read_text(encoding="utf-8-sig")
    assert "Derecho Informático" in manifest


# --- defect 3: the root index was a dead end -------------------------------------


def test_the_root_marks_a_concept_that_has_a_subtree_below_it(bundle):
    okf.write_indexes(bundle, "0.2", "T")
    index = (bundle / "index.md").read_text(encoding="utf-8-sig")
    assert "[Delitos Informáticos en Argentina](./delitos.md) - Marco legal." in index
    assert "([2 more](./delitos/index.md))" in index


def test_a_directory_no_concept_owns_is_still_reachable_from_the_root(bundle):
    okf.write_indexes(bundle, "0.2", "T")
    index = (bundle / "index.md").read_text(encoding="utf-8-sig")
    assert "[Suelto](./suelto/index.md) - 1 concept(s)" in index


# --- the general pass ------------------------------------------------------------


def test_every_directory_gets_an_index(bundle):
    okf.write_indexes(bundle, "0.2", "T")
    for path in (bundle, bundle / "delitos", bundle / "suelto"):
        assert (path / "index.md").exists(), path
    assert not [f for f in okf.validate(bundle, use_okflint=False).warnings if f.code == "W4"]


def test_only_the_bundle_root_index_carries_frontmatter(bundle):
    okf.write_indexes(bundle, "0.2", "T")
    assert (bundle / "index.md").read_text(encoding="utf-8-sig").startswith("---")
    for name in ("delitos", "suelto"):
        text = (bundle / name / "index.md").read_text(encoding="utf-8-sig")
        assert not text.startswith("---")  # E3
    assert not okf.validate(bundle, use_okflint=False).errors


def test_a_subdirectory_index_is_headed_by_the_concept_that_owns_it(bundle):
    okf.write_indexes(bundle, "0.2", "T")
    nested = (bundle / "delitos" / "index.md").read_text(encoding="utf-8-sig")
    assert nested.startswith("# Delitos Informáticos en Argentina")


def test_a_grandchild_directory_is_counted_by_its_grandparent(bundle):
    okf.write_concept(bundle, concept("delitos/tipos/fraude", "Fraude Informático"))
    okf.write_indexes(bundle, "0.2", "T")

    assert (bundle / "delitos" / "tipos" / "index.md").exists()
    root = (bundle / "index.md").read_text(encoding="utf-8-sig")
    assert "([3 more](./delitos/index.md))" in root, "the count must include the whole subtree"


def test_a_deprecated_concept_is_marked_in_the_index(bundle):
    okf.set_status(bundle, "suelto/nota", "deprecated")
    okf.write_indexes(bundle, "0.2", "T")
    nested = (bundle / "suelto" / "index.md").read_text(encoding="utf-8-sig")
    assert "[Una Nota Suelta](./nota.md) *(deprecated)*" in nested


# --- read off disk, not accumulated during the run -------------------------------


def test_a_skipped_source_is_still_indexed(project, monkeypatch):
    (project / "sources" / "pricing.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    # only one source changes; the other is skipped and never re-read by the run
    (project / "sources" / "pricing.md").write_text(SAMPLE + "\nMore.\n", encoding="utf-8")
    pipeline.ingest(cfg, dry_run=False)

    index = (cfg.bundle_root / "index.md").read_text(encoding="utf-8-sig")
    assert "./revenue-policy.md" in index and "./pricing.md" in index


def test_a_hand_edited_title_wins_over_what_ragged_synthesized(project, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    cfg = config_module.load(project)
    pipeline.ingest(cfg, dry_run=False)

    path = cfg.bundle_root / "revenue-policy.md"
    edited = okf.parse(path.read_text(encoding="utf-8-sig"), "revenue-policy")
    edited.title = "Revenue Recognition (reviewed)"
    okf.write_concept(cfg.bundle_root, edited)

    (project / "sources" / "other.md").write_text(SAMPLE, encoding="utf-8")
    pipeline.ingest(cfg, dry_run=False)

    index = (cfg.bundle_root / "index.md").read_text(encoding="utf-8-sig")
    assert "[Revenue Recognition (reviewed)](./revenue-policy.md)" in index


def test_the_segmented_bundle_still_discloses_progressively(segmented):
    cfg = config_module.load(segmented)
    pipeline.ingest(cfg, dry_run=False)
    root = (cfg.bundle_root / "index.md").read_text(encoding="utf-8-sig")

    assert "definicion.md" not in root  # children stay one hop away
    assert "([2 more](./trabajo-practico/index.md))" in root
