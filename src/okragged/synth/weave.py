"""Link-weave: a second pass that turns a set of concepts into a graph (§6.5).

Runs after every concept for the run exists, because a link can only point at a
concept that has been written. Rewrites bodies and nothing else — the schema
returns `body_markdown` alone, so a pass that wanders cannot change a title, a
type or a source.

The spec says a link asserts a relationship and the surrounding prose is what
names it, so this pass must not produce a "Related" section. That is the one
decision the wiki framing actually makes here.
"""

from __future__ import annotations

import json
import posixpath
import re

from .base import (
    WEAVE_SCHEMA,
    SynthError,
    WeaveResult,
    anchor_footnote_defs,
)
from .prompt import weave_system_prompt, weave_user_prompt
from .providers import resolve

# the target group allows spaces, which markdown does not: an inverted link puts
# the label there, and we can only repair what we can see
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def weave(
    *,
    concept_id: str,
    title: str,
    body: str,
    index: list[tuple[str, str, str]],
    parent: str | None = None,
    candidates: list[str] | None = None,
    provider: str,
    model: str | None = None,
    effort: str = "high",
    max_tokens: int = 16000,
) -> WeaveResult:
    impl = resolve(provider)
    response = impl.call(
        # the whole bundle index, identical for every concept in the pass
        system=weave_system_prompt(index),
        user=weave_user_prompt(
            concept_id, title, body, parent=parent, candidates=candidates or []
        ),
        schema=WEAVE_SCHEMA,
        model=model or impl.DEFAULT_MODEL,
        effort=effort,
        max_tokens=max_tokens,
    )

    try:
        data = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise SynthError(f"{provider} returned an unparseable woven body: {exc}") from exc
    if not data.get("body_markdown"):
        raise SynthError(f"{provider} returned an empty woven body")

    woven, invented = prune_links(
        str(data["body_markdown"]).strip(),
        {cid: name for cid, name, _ in index},
        concept_id,
    )
    return WeaveResult(
        # this pass rewrites the body, so a definition it inlines is its own to fix
        body_markdown=anchor_footnote_defs(woven),
        invented=invented,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
    )


def prune_links(body: str, titles: dict[str, str], concept_id: str) -> tuple[str, list[str]]:
    """Repair what the model got wrong about links, mechanically.

    Three failure modes, all three observed on a real corpus, all three
    unambiguous enough to fix rather than report:

    *Inverted syntax* — `[/finance/pricing.md](list prices)` instead of
    `[list prices](/finance/pricing.md)`. Swapped when the label side resolves
    to a concept and the target side does not.

    *A path where the label should be* — `[/finance/pricing.md](/finance/pricing.md)`.
    The link works, which is why no linter objects, but a raw path is not a
    label; it becomes the target concept's title.

    *An invented target* — a path that is not a concept becomes plain text again
    and is reported. Broken links are spec-legal and stay warnings when a *human*
    writes one forward at knowledge not yet written; a model handed the full
    index did not write forward, it guessed.
    """
    here = posixpath.dirname(concept_id)
    invented: list[str] = []

    def concept_at(candidate: str) -> str | None:
        clean = candidate.strip().split("#", 1)[0].split("?", 1)[0]
        if "://" in candidate or not clean.endswith(".md"):
            return None
        raw = clean.lstrip("/") if clean.startswith("/") else posixpath.join(here, clean)
        resolved = posixpath.normpath(raw).removesuffix(".md")
        return resolved if resolved in titles and resolved != concept_id else None

    def repair(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2)
        hit = concept_at(target)
        if hit is not None:
            if not _path_shaped(text):
                return match.group(0)
            return f"[{titles.get(hit) or text}](/{hit}.md)"
        flipped = concept_at(text)
        if flipped is not None:
            return f"[{target}](/{flipped}.md)"
        # whichever side holds the path, it points at nothing: keep the label
        if _path_shaped(target):
            invented.append(target)
            return text
        if _path_shaped(text):
            invented.append(text)
            return target
        return match.group(0)  # not an attempt at a concept link

    return LINK_RE.sub(repair, body), invented


def _path_shaped(candidate: str) -> bool:
    clean = candidate.strip().split("#", 1)[0].split("?", 1)[0]
    return "://" not in candidate and clean.endswith(".md")
