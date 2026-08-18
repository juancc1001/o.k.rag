"""Planning pass: one source document -> a parent plus N section slices.

Two passes, because a single call that returns an array of concepts makes every
concept share one `max_tokens` budget — which is how M1 produced a flat ~2.5k
ceiling per concept regardless of whether the source was 15k or 39k characters.
Here each section gets its own call and its own budget.

The plan carries a verbatim `start_marker` per section rather than character
offsets: models do not count reliably, but they do quote reliably. Cutting is
done here, in code, against the real text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .base import (
    MAX_CONCEPT_WORDS,
    MIN_SECTION_WORDS,
    OUTLINE_SCHEMA,
    SynthError,
)
from .prompt import outline_system_prompt, user_prompt
from .providers import resolve

# how much of a marker to match on; long quotes drift, short ones collide
MARKER_CHARS = 120


@dataclass
class Section:
    title: str
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Outline:
    type: str
    title: str
    description: str
    overview: str
    tags: list[str]
    sections: list[Section]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    unmatched: list[str] = None  # markers the model wrote that are not in the text

    def __post_init__(self) -> None:
        if self.unmatched is None:
            self.unmatched = []


def needs_segmenting(text: str) -> bool:
    return len(text.split()) > MAX_CONCEPT_WORDS


def plan(
    *,
    text: str,
    source_id: str,
    source_uri: str,
    source_title: str,
    allowed_types: list[str],
    provider: str,
    model: str | None = None,
    effort: str = "high",
    max_tokens: int = 16000,
) -> Outline:
    impl = resolve(provider)
    response = impl.call(
        system=outline_system_prompt(allowed_types),
        user=user_prompt(source_id, source_uri, source_title, text),
        schema=OUTLINE_SCHEMA,
        model=model or impl.DEFAULT_MODEL,
        effort=effort,
        max_tokens=max_tokens,
    )

    try:
        data = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise SynthError(f"{provider} returned an unparseable outline: {exc}") from exc

    missing = [k for k in ("type", "title", "description", "overview") if not data.get(k)]
    if missing:
        raise SynthError(f"{provider} omitted outline field(s): {', '.join(missing)}")

    planned = data.get("sections") or []
    if not planned:
        raise SynthError(f"{provider} returned an outline with no sections")

    sections, unmatched = cut(text, planned)
    if not sections:
        raise SynthError(
            f"{provider} proposed {len(planned)} section(s) but none of the start "
            "markers appear in the source text"
        )

    return Outline(
        type=str(data["type"]).strip(),
        title=str(data["title"]).strip(),
        description=str(data["description"]).strip(),
        overview=str(data["overview"]).strip(),
        tags=[str(t).strip() for t in data.get("tags") or [] if str(t).strip()],
        sections=sections,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
        unmatched=unmatched,
    )


def cut(text: str, planned: list[dict]) -> tuple[list[Section], list[str]]:
    """Slice `text` at each section's start_marker. Pure; no model involved.

    Returns the sections plus the titles whose marker could not be located, so
    the caller can report dropped coverage rather than silently losing text.
    """
    flat, offsets = _flatten(text)
    hits: list[tuple[int, str]] = []
    unmatched: list[str] = []

    for entry in planned:
        title = str(entry.get("title") or "").strip() or "Untitled"
        marker = _norm(str(entry.get("start_marker") or ""))[:MARKER_CHARS]
        at = flat.find(marker) if marker else -1
        if at < 0:
            unmatched.append(title)
            continue
        hits.append((offsets[at], title))

    # trust document order over the order the model listed them in
    hits.sort(key=lambda pair: pair[0])
    hits = _dedupe(hits)
    if not hits:
        return [], unmatched

    # text before the first marker belongs to the first section
    hits[0] = (0, hits[0][1])

    sections = [
        Section(title=title, text=text[start:end].strip())
        for (start, title), end in zip(hits, [h[0] for h in hits[1:]] + [len(text)])
    ]
    return _fold_short(sections), unmatched


def _dedupe(hits: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Two markers resolving to the same offset would make an empty section."""
    out: list[tuple[int, str]] = []
    for offset, title in hits:
        if out and offset == out[-1][0]:
            continue
        out.append((offset, title))
    return out


def _fold_short(sections: list[Section]) -> list[Section]:
    """Below ~150 words of substance a section is not a concept (§4.3)."""
    folded: list[Section] = []
    for section in sections:
        if not section.text:
            continue
        if folded and section.words < MIN_SECTION_WORDS:
            previous = folded[-1]
            folded[-1] = Section(previous.title, f"{previous.text}\n\n{section.text}")
        else:
            folded.append(section)
    # a leading short section has no previous to fold into; merge it forward
    if len(folded) > 1 and folded[0].words < MIN_SECTION_WORDS:
        head, nxt = folded[0], folded[1]
        folded[1] = Section(nxt.title, f"{head.text}\n\n{nxt.text}")
        folded.pop(0)
    return folded


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _flatten(text: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed, case-folded text plus a map back to raw offsets.

    Extracted PDF text breaks lines mid-sentence, so a marker quoted from the
    rendered document rarely matches the raw string. Match on the flattened
    form, then report the original offset.
    """
    chars: list[str] = []
    offsets: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if in_space:
                continue
            chars.append(" ")
            offsets.append(index)
            in_space = True
        else:
            chars.append(char)
            offsets.append(index)
            in_space = False
    return "".join(chars).casefold(), offsets
