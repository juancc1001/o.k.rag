from __future__ import annotations

import json

import pytest

from ragged.synth import (
    MAX_CONCEPT_WORDS,
    MIN_SECTION_WORDS,
    ProviderResponse,
    SynthError,
    cut,
    needs_segmenting,
    plan,
)
from ragged.synth.providers import gemini

FILLER = "relleno " * (MIN_SECTION_WORDS + 20)

# line breaks land mid-sentence, the way markitdown leaves extracted PDF text
TEXT = (
    "Portada, materia y datos del alumno.\n\n"
    "Los delitos informáticos comprenden conductas ilícitas cometidas\n"
    "mediante el uso de tecnologías de la información.\n\n"
    f"{FILLER}\n\n"
    "El profesor mexicano Julio Tellez Valdés define los delitos\n"
    "informáticos como actitudes ilícitas.\n\n"
    f"{FILLER}"
)

FIRST = "Los delitos informáticos comprenden conductas ilícitas cometidas mediante el uso"
SECOND = "El profesor mexicano Julio Tellez Valdés define los delitos informáticos"


def _planned(*pairs):
    return [{"title": t, "start_marker": m} for t, m in pairs]


# --- cut: the pure slicing pass -------------------------------------------------


def test_a_marker_matches_across_the_line_breaks_of_extracted_text():
    sections, unmatched = cut(TEXT, _planned(("Definición", FIRST), ("Doctrina", SECOND)))

    assert not unmatched
    assert [s.title for s in sections] == ["Definición", "Doctrina"]
    assert "Tellez Valdés" in sections[1].text
    assert "Tellez Valdés" not in sections[0].text


def test_text_before_the_first_marker_is_not_lost():
    sections, _ = cut(TEXT, _planned(("Definición", FIRST), ("Doctrina", SECOND)))
    assert "Portada" in sections[0].text


def test_every_character_of_the_source_lands_in_some_section():
    sections, _ = cut(TEXT, _planned(("Definición", FIRST), ("Doctrina", SECOND)))
    joined = "".join(s.text for s in sections)
    # stripping per section loses only whitespace
    assert len(joined) >= len(TEXT.strip()) - 4 * len(sections)


def test_an_unfound_marker_is_reported_rather_than_silently_dropped():
    sections, unmatched = cut(
        TEXT, _planned(("Definición", FIRST), ("Inventada", "esta frase no está en el texto"))
    )
    assert unmatched == ["Inventada"]
    assert [s.title for s in sections] == ["Definición"]


def test_sections_are_ordered_by_position_not_by_how_the_model_listed_them():
    sections, _ = cut(TEXT, _planned(("Doctrina", SECOND), ("Definición", FIRST)))
    assert [s.title for s in sections] == ["Definición", "Doctrina"]


def test_a_short_section_folds_into_the_previous_one():
    text = f"{FILLER}\n\nApenas unas palabras sueltas.\n\nmás {FILLER}"
    sections, _ = cut(
        text,
        _planned(
            ("Larga", FILLER.split()[0]),
            ("Corta", "Apenas unas palabras sueltas"),
            ("Otra larga", "más relleno"),
        ),
    )
    assert [s.title for s in sections] == ["Larga", "Otra larga"]
    assert "Apenas unas palabras" in sections[0].text


def test_a_leading_short_section_folds_forward():
    text = f"Dos palabras.\n\n{FILLER}"
    sections, _ = cut(
        text, _planned(("Corta", "Dos palabras"), ("Larga", FILLER.split()[0]))
    )
    assert [s.title for s in sections] == ["Larga"]
    assert "Dos palabras" in sections[0].text


def test_two_markers_on_the_same_offset_do_not_make_an_empty_section():
    sections, _ = cut(TEXT, _planned(("A", FIRST), ("B", FIRST)))
    assert len(sections) == 1
    assert all(s.text for s in sections)


def test_no_marker_found_at_all_yields_nothing_to_synthesize():
    sections, unmatched = cut(TEXT, _planned(("X", "nada de esto aparece")))
    assert sections == []
    assert unmatched == ["X"]


# --- the split threshold --------------------------------------------------------


def test_short_documents_are_not_segmented():
    assert not needs_segmenting("palabra " * (MAX_CONCEPT_WORDS - 1))


def test_long_documents_are_segmented():
    assert needs_segmenting("palabra " * (MAX_CONCEPT_WORDS + 1))


# --- plan: the outline call -----------------------------------------------------


OUTLINE = {
    "type": "Document",
    "title": "TP de delitos informáticos",
    "description": "Trabajo práctico sobre delitos informáticos en Argentina.",
    "overview": "Cubre la definición doctrinaria y el marco normativo.",
    "tags": ["Derecho Penal"],
    "sections": [
        {"title": "Definición", "start_marker": FIRST},
        {"title": "Doctrina", "start_marker": SECOND},
    ],
}


def _plan(monkeypatch, payload):
    monkeypatch.setattr(gemini, "call", lambda **_: ProviderResponse(text=json.dumps(payload)))
    return plan(
        text=TEXT,
        source_id="tp",
        source_uri="sources/tp.pdf",
        source_title="tp",
        allowed_types=["Document"],
        provider="gemini",
    )


def test_plan_returns_the_parent_identity_and_the_cut_sections(monkeypatch):
    outline = _plan(monkeypatch, OUTLINE)
    assert outline.title == "TP de delitos informáticos"
    assert outline.overview.startswith("Cubre")
    assert [s.title for s in outline.sections] == ["Definición", "Doctrina"]
    assert not outline.unmatched


def test_plan_rejects_an_outline_missing_the_parent_description(monkeypatch):
    payload = {k: v for k, v in OUTLINE.items() if k != "description"}
    with pytest.raises(SynthError, match="omitted outline field"):
        _plan(monkeypatch, payload)


def test_plan_rejects_an_outline_with_no_sections(monkeypatch):
    with pytest.raises(SynthError, match="no sections"):
        _plan(monkeypatch, {**OUTLINE, "sections": []})


def test_plan_fails_loudly_when_no_marker_can_be_located(monkeypatch):
    payload = {**OUTLINE, "sections": _planned(("X", "frase inventada que no aparece"))}
    with pytest.raises(SynthError, match="none of the start markers"):
        _plan(monkeypatch, payload)


def test_plan_reports_partially_unmatched_markers_without_failing(monkeypatch):
    payload = {
        **OUTLINE,
        "sections": _planned(("Definición", FIRST), ("Inventada", "no aparece en el texto")),
    }
    outline = _plan(monkeypatch, payload)
    assert outline.unmatched == ["Inventada"]
    assert [s.title for s in outline.sections] == ["Definición"]
