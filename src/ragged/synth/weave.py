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
)
from .prompt import weave_system_prompt, weave_user_prompt
from .providers import resolve

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def weave(
    *,
    concept_id: str,
    title: str,
    body: str,
    index: list[tuple[str, str]],
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
        str(data["body_markdown"]).strip(), {cid for cid, _ in index}, concept_id
    )
    return WeaveResult(
        body_markdown=woven,
        invented=invented,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
    )


def prune_links(body: str, known: set[str], concept_id: str) -> tuple[str, list[str]]:
    """Unwrap markdown links that do not point at a concept in the bundle.

    Broken links are spec-legal and stay warnings when a *human* writes one
    forward at knowledge not yet written. A model handed the full index that
    emits a path outside it did not write forward, it guessed — so the link
    becomes plain text again and the target is reported.
    """
    here = posixpath.dirname(concept_id)
    invented: list[str] = []

    def resolve_target(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2)
        clean = target.split("#", 1)[0].split("?", 1)[0]
        if "://" in target or not clean.endswith(".md"):
            return match.group(0)
        raw = clean.lstrip("/") if clean.startswith("/") else posixpath.join(here, clean)
        resolved = posixpath.normpath(raw).removesuffix(".md")
        if resolved in known and resolved != concept_id:
            return match.group(0)
        invented.append(target)
        return text

    return LINK_RE.sub(resolve_target, body), invented
