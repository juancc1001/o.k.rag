"""M2 item 3: link-weave (§6.5).

`prune_links` is pure, so the repair half is tested without a model. The pass
itself is stubbed like every other model call in this suite.
"""

from __future__ import annotations

import json

import pytest

from okragged import config as config_module
from okragged import okf, pipeline, synth
from okragged.ledger import LEDGER_NAME, Ledger, body_hash
from okragged.synth import ProviderHTTPError, ProviderResponse, SynthError, WeaveResult, prune_links
from okragged.synth.prompt import system_prompt, weave_system_prompt, weave_user_prompt
from okragged.synth.providers import gemini

from test_pipeline import (  # noqa: F401  (project/segmented are used as fixtures)
    SAMPLE,
    fake_synthesize,
    project,
    segmented,
)

KNOWN = {"finance/revenue-policy": "Revenue Recognition Policy", "pricing": "Pricing"}


# --- the deterministic repair ----------------------------------------------------


def test_a_link_to_a_real_concept_survives():
    body = "Deferred under the [policy](/finance/revenue-policy.md)."
    assert prune_links(body, KNOWN, "pricing") == (body, [])


def test_an_invented_target_becomes_plain_text_and_is_reported():
    woven, invented = prune_links("See the [tax rules](/finance/tax.md).", KNOWN, "pricing")
    assert woven == "See the tax rules."
    assert invented == ["/finance/tax.md"]


def test_a_concept_cannot_link_to_itself():
    woven, invented = prune_links("As [described here](/pricing.md).", KNOWN, "pricing")
    assert woven == "As described here."
    assert invented == ["/pricing.md"]


def test_a_relative_link_that_resolves_to_a_concept_survives():
    body = "See [the policy](./revenue-policy.md)."
    assert prune_links(body, KNOWN, "finance/pricing") == (body, [])


def test_an_external_url_and_a_non_markdown_target_are_left_alone():
    body = "[spec](https://example.org/okf.md) and [chart](./chart.png)"
    assert prune_links(body, KNOWN, "pricing") == (body, [])


def test_footnotes_survive_the_repair():
    body = "A claim.[^src]\n\n[^src]: Handbook"
    assert prune_links(body, KNOWN, "pricing") == (body, [])


def test_an_inverted_link_is_swapped_back():
    """Observed on the real corpus: the label and the path change places."""
    woven, invented = prune_links(
        "establecida por la [/finance/revenue-policy.md](Ley 26.388), que...",
        KNOWN,
        "pricing",
    )
    assert woven == "establecida por la [Ley 26.388](/finance/revenue-policy.md), que..."
    assert invented == []


def test_an_inverted_link_to_nothing_is_still_reported():
    woven, invented = prune_links("the [/finance/tax.md](tax rules).", KNOWN, "pricing")
    assert woven == "the tax rules."
    assert invented == ["/finance/tax.md"]


def test_prose_in_parentheses_after_a_bracket_is_not_a_link():
    body = "The rule [see below](not a path at all) applies."
    assert prune_links(body, KNOWN, "pricing") == (body, [])


def test_a_raw_path_used_as_the_label_becomes_the_target_title():
    """The link works, so no linter objects — but a path is not a label."""
    woven, invented = prune_links(
        "detallado en [/finance/revenue-policy.md](/finance/revenue-policy.md).",
        KNOWN,
        "pricing",
    )
    assert woven == "detallado en [Revenue Recognition Policy](/finance/revenue-policy.md)."
    assert invented == []


# --- the prompts -----------------------------------------------------------------


def test_the_synthesis_prefix_carries_the_existing_concept_index():
    prefix = system_prompt(["Document"], [("pricing", "How list prices are set.")])
    assert "`/pricing.md` - How list prices are set." in prefix


def test_an_empty_bundle_says_so_rather_than_omitting_the_section():
    # the section must exist either way, or the prefix is not stable run to run
    assert "the bundle is empty" in system_prompt(["Document"], [])


def test_the_weave_prefix_holds_the_index_and_the_user_message_holds_the_concept():
    index = [("pricing", "Pricing", "How list prices are set."), ("tp/uno", "Uno", "Uno.")]
    prefix = weave_system_prompt(index)
    user = weave_user_prompt("tp/uno", "Uno", "the body text", parent="tp", candidates=["pricing"])

    assert "`/pricing.md`" in prefix
    # anything per-concept in the prefix would stop it caching after unit one
    assert "the body text" not in prefix and "Rewrite the body" not in prefix
    assert "/tp.md" in user and "Link back to the parent" in user
    assert "`/pricing.md`" in user


# --- the provider call -----------------------------------------------------------


def weave_via(monkeypatch, text: str, seen: dict | None = None):
    def call(**kwargs):
        if seen is not None:
            seen.update(kwargs)
        return ProviderResponse(text=text)

    monkeypatch.setattr(gemini, "call", call)
    return synth.weave(
        concept_id="pricing",
        title="Pricing",
        body="Original body.",
        index=[("pricing", "Pricing", "p"), ("finance/revenue-policy", "Revenue", "r")],
        provider="gemini",
    )


def test_weave_asks_for_the_body_and_nothing_else(monkeypatch):
    seen: dict = {}
    result = weave_via(monkeypatch, json.dumps({"body_markdown": "Woven."}), seen)

    assert result.body_markdown == "Woven."
    assert seen["schema"]["required"] == ["body_markdown"]
    assert seen["schema"]["additionalProperties"] is False


def test_weave_prunes_an_invented_target_before_the_body_is_returned(monkeypatch):
    payload = json.dumps({"body_markdown": "See [tax](/finance/tax.md)."})
    result = weave_via(monkeypatch, payload)
    assert result.body_markdown == "See tax."
    assert result.invented == ["/finance/tax.md"]


def test_weave_reports_unparseable_json(monkeypatch):
    with pytest.raises(SynthError, match="unparseable woven body"):
        weave_via(monkeypatch, "not json")


def test_weave_rejects_an_empty_body(monkeypatch):
    with pytest.raises(SynthError, match="empty woven body"):
        weave_via(monkeypatch, json.dumps({"body_markdown": ""}))


# --- the pass --------------------------------------------------------------------


def link_to_the_other(**kwargs) -> WeaveResult:
    """Weave a link to whichever concept in the index is not this one."""
    concept_id = kwargs["concept_id"]
    target = next(cid for cid, *_ in kwargs["index"] if cid != concept_id)
    return WeaveResult(
        body_markdown=f"{kwargs['body']}\n\nSee [the other one](/{target}.md).",
        invented=[],
    )


@pytest.fixture
def two_sources(project, monkeypatch):
    (project / "sources" / "pricing.md").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(pipeline, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline, "weave", link_to_the_other)
    return project


def test_the_weave_pass_links_concepts_to_each_other(two_sources):
    cfg = config_module.load(two_sources)
    summary = pipeline.ingest(cfg, dry_run=False)

    body = (cfg.bundle_root / "pricing.md").read_text(encoding="utf-8-sig")
    assert "(/revenue-policy.md)" in body
    assert sorted(c for o in summary.outcomes for c in o.linked) == [
        "pricing",
        "revenue-policy",
    ]
    assert summary.report is not None and summary.report.ok
    assert not [w for w in summary.report.warnings if w.code == "W2"]


def test_every_child_is_told_its_parent_so_it_can_link_back(segmented, monkeypatch):
    """§4.3 wants the back-link in prose, so the pass has to know the parent."""
    seen: dict[str, str | None] = {}

    def record_parent(**kwargs):
        seen[kwargs["concept_id"]] = kwargs["parent"]
        return WeaveResult(body_markdown=kwargs["body"], invented=[])

    monkeypatch.setattr(pipeline, "weave", record_parent)
    pipeline.ingest(config_module.load(segmented), dry_run=False)

    assert seen["trabajo-practico/definicion"] == "trabajo-practico"
    assert seen["trabajo-practico"] is None


def test_the_ledger_records_the_woven_body_not_the_unwoven_one(two_sources):
    """Otherwise the next run reads every concept as hand-edited (§13, item 3)."""
    cfg = config_module.load(two_sources)
    pipeline.ingest(cfg, dry_run=False)

    (two_sources / "sources" / "pricing.md").write_text(SAMPLE + "\nMore.\n", encoding="utf-8")
    second = pipeline.ingest(cfg, dry_run=False)

    outcome = next(o for o in second.written if o.uri == "sources/pricing.md")
    assert outcome.protected == {}

    ledger = Ledger.load(cfg.sidecar_root / LEDGER_NAME)
    text = (cfg.bundle_root / "pricing.md").read_text(encoding="utf-8-sig")
    assert ledger.concepts["pricing"].body_sha256 == body_hash(okf.parse(text, "pricing").body)


def test_an_invented_link_is_unwrapped_and_reported(two_sources, monkeypatch):
    def link_to_nothing(**kwargs):
        return WeaveResult(
            body_markdown=f"{kwargs['body']}\n\nSee [the tax rules](/finance/tax.md).",
            invented=[],
        )

    # go through the real prune_links by calling it the way weave() does
    def weave_then_prune(**kwargs):
        result = link_to_nothing(**kwargs)
        body, invented = prune_links(
            result.body_markdown,
            {cid: name for cid, name, _ in kwargs["index"]},
            kwargs["concept_id"],
        )
        return WeaveResult(body_markdown=body, invented=invented)

    monkeypatch.setattr(pipeline, "weave", weave_then_prune)
    cfg = config_module.load(two_sources)
    summary = pipeline.ingest(cfg, dry_run=False)

    body = (cfg.bundle_root / "pricing.md").read_text(encoding="utf-8-sig")
    assert "See the tax rules." in body
    assert "/finance/tax.md" in summary.invented_links
    assert not [w for w in summary.report.warnings if w.code == "W2"]


def test_a_failed_weave_keeps_the_unwoven_concept(two_sources, monkeypatch):
    def quota_exceeded(**kwargs):
        if kwargs["concept_id"] == "pricing":
            raise ProviderHTTPError("gemini", 429, "quota")
        return link_to_the_other(**kwargs)

    monkeypatch.setattr(pipeline, "weave", quota_exceeded)
    cfg = config_module.load(two_sources)
    summary = pipeline.ingest(cfg, dry_run=False)

    assert (cfg.bundle_root / "pricing.md").exists()
    errors = [e for o in summary.outcomes for e in o.weave_errors]
    assert len(errors) == 1 and errors[0].startswith("pricing:")
    assert summary.report is not None and summary.report.ok


def test_a_concept_a_human_owns_is_not_woven(two_sources, monkeypatch):
    cfg = config_module.load(two_sources)
    pipeline.ingest(cfg, dry_run=False)

    path = cfg.bundle_root / "pricing.md"
    concept = okf.parse(path.read_text(encoding="utf-8-sig"), "pricing")
    concept.verified = [{"by": "human:juan", "at": "2026-08-05T00:00:00Z"}]
    body_before = concept.body
    okf.write_concept(cfg.bundle_root, concept)

    for name in ("pricing.md", "revenue-policy.md"):
        (two_sources / "sources" / name).write_text(SAMPLE + "\nMore.\n", encoding="utf-8")
    pipeline.ingest(cfg, dry_run=False)

    assert okf.parse(path.read_text(encoding="utf-8-sig"), "pricing").body == body_before


def test_a_run_that_synthesized_nothing_weaves_nothing(two_sources, monkeypatch):
    cfg = config_module.load(two_sources)
    pipeline.ingest(cfg, dry_run=False)

    def no_model(**_):
        raise AssertionError("an unchanged bundle must not reach the model")

    monkeypatch.setattr(pipeline, "synthesize", no_model)
    monkeypatch.setattr(pipeline, "weave", no_model)
    second = pipeline.ingest(cfg, dry_run=False)
    assert len(second.skipped) == 2


def test_synthesis_candidate_links_reach_the_weave_pass(two_sources, monkeypatch):
    def with_candidates(**kwargs):
        result = fake_synthesize(**kwargs)
        result.candidate_links = ["revenue-policy"]
        return result

    seen: dict[str, list[str]] = {}

    def record_candidates(**kwargs):
        seen[kwargs["concept_id"]] = kwargs["candidates"]
        return WeaveResult(body_markdown=kwargs["body"], invented=[])

    monkeypatch.setattr(pipeline, "synthesize", with_candidates)
    monkeypatch.setattr(pipeline, "weave", record_candidates)
    pipeline.ingest(config_module.load(two_sources), dry_run=False)

    assert seen["pricing"] == ["revenue-policy"]


def test_the_synthesis_index_is_the_bundle_as_it_was_before_the_run(two_sources, monkeypatch):
    """A prefix that grew as concepts landed would stop caching mid-run."""
    seen: list[tuple[str, ...]] = []

    def record_index(**kwargs):
        seen.append(tuple(cid for cid, _ in kwargs["index"]))
        return fake_synthesize(**kwargs)

    monkeypatch.setattr(pipeline, "synthesize", record_index)
    cfg = config_module.load(two_sources)
    pipeline.ingest(cfg, dry_run=False)
    assert seen == [(), ()]

    seen.clear()
    for name in ("pricing.md", "revenue-policy.md"):
        (two_sources / "sources" / name).write_text(SAMPLE + "\nMore.\n", encoding="utf-8")
    pipeline.ingest(cfg, dry_run=False)
    assert seen == [("pricing", "revenue-policy"), ("pricing", "revenue-policy")]

def test_a_definition_the_woven_body_left_mid_line_gets_its_own_line(monkeypatch):
    inlined = "A claim.[^pricing] [^pricing]: Pricing"
    result = weave_via(monkeypatch, json.dumps({"body_markdown": inlined}))
    assert result.body_markdown == "A claim.[^pricing]\n\n[^pricing]: Pricing"
