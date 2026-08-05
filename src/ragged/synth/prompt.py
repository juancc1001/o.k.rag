"""The synthesis, planning and link-weave system prompts.

Each is a cached prefix: it MUST be byte-identical across every unit in its pass
or prompt caching silently stops working. The only things that vary are the type
vocabulary and the bundle's concept index, both snapshotted once per run.
"""

from __future__ import annotations

SYSTEM = """\
You author concept documents for an Open Knowledge Format (OKF) v0.2 bundle.

OKF represents organizational knowledge as markdown files with YAML frontmatter.
One file is one concept: a single self-contained unit of knowledge that a reader
or an agent can consume on its own.

## Your task

You are given the extracted text of one source document. Produce one concept
document from it.

## Rules

1. `type` — pick the kind of concept this is. Reuse a value from the vocabulary
   below when one fits. Only propose a new value when nothing fits; it will be
   reported for review, so do not invent casually.

2. `title` — human-readable display name. Describe the knowledge, not the file.
   "Q3 Revenue Recognition Policy", not "report_final_v2".

3. `description` — exactly one sentence stating what this concept contains. It
   is shown in directory listings and search results, so it must be useful in
   isolation, without the title next to it.

4. `body_markdown` — the concept body. Prefer structural markdown (headings,
   lists, tables, fenced code blocks) over free prose. Use the conventional
   heading `# Schema` for column/field descriptions of a data asset, and
   `# Examples` for concrete usage in code blocks. Do not repeat the title as a
   heading and do not restate the description as the first line.

5. Attribution — the source is given to you with an id. Where a specific claim
   in the body derives from that source, mark it with a markdown footnote
   reference using that id, e.g. `The table is sharded daily.[^ga4-schema]`.
   Define the footnote once at the end of the body:
   `[^ga4-schema]: GA4 BigQuery Export schema`. Use the id verbatim.

6. `tags` — cross-cutting categories only. Omit rather than emit a tag that
   would apply to exactly one concept in the bundle.

7. `candidate_links` — ids from the bundle index below that this concept
   genuinely relates to. A later pass weaves them into the prose, so name only
   what a reader of *this* concept would want to follow. Omit rather than guess,
   and never name an id that is not in the index.

## Hard constraints

- Never invent facts, figures, names, URLs, column names, or dates that are not
  in the source text. If the source is unclear on something, omit it.
- Do not summarise away the substance. The concept replaces the source document
  as the thing an agent reads, so load-bearing detail must survive.
- Write the body as knowledge, not as a report about a document. Avoid "This
  document describes..." — state the thing itself.
- Do not include frontmatter delimiters or YAML in `body_markdown`. You return
  structured fields; the frontmatter is assembled from them.
"""


OUTLINE_SYSTEM = """\
You plan how one source document becomes a set of Open Knowledge Format (OKF)
concept documents.

A concept is a single self-contained unit of knowledge that a reader or an agent
can consume on its own. One source document is usually several concepts: a
document is a container, a concept is a unit of meaning. Splitting is the norm,
not the exception.

## Your task

You are given the extracted text of one source document. Do not write the
concepts. Produce the plan: the parent concept's identity, and the list of
sections that will each become one child concept.

## Rules

1. `sections` — one entry per substantive section, in the order they appear in
   the text. Aim for units of roughly 150 to 1200 words of source text. Split a
   longer run of text into several sections rather than emitting one huge one.
   Do not create a section for front matter, tables of contents, bibliographies,
   or author bylines with no substance of their own.

2. `start_marker` — the first 8 to 15 words of that section, copied **verbatim**
   from the source text. This is a search key used to cut the text, so it must
   appear in the source exactly as you write it, and it must be unique enough to
   match only that section's start. Do not paraphrase, translate, fix spelling,
   or normalise punctuation. Copy the characters.

3. `title` per section — human-readable name for the knowledge in that section,
   not "Section 3".

4. `type`, `title`, `description`, `tags` — for the **parent** concept, which
   represents the document as a whole. Same rules as a concept: `description` is
   exactly one sentence useful in isolation.

5. `overview` — a few sentences of prose stating what the document as a whole
   covers and how its parts relate. This becomes the parent concept's body, so
   write knowledge, not "This document contains four sections".

## Hard constraints

- Never invent a `start_marker`. If you cannot copy it verbatim, do not emit
  that section.
- Cover the whole document. Every substantive passage must fall inside some
  section.
- A document with genuinely one topic and under ~1200 words gets one section.
"""


WEAVE_SYSTEM = """\
You add cross-links to one concept document in an Open Knowledge Format (OKF)
bundle.

A bundle is a wiki, and what makes it more than a folder of files is that a
reader who meets a term can follow a link to the concept that explains it. Every
concept has already been written. Your only job is the links between them.

## Rules

1. Link where a reader would want to follow: a term this body uses and another
   concept defines, a document it refers to, a process it is one step of.

2. Links go **inline, in the prose, on the words that already carry the
   meaning** — `deferred under the [revenue recognition
   policy](/finance/revenue-policy.md)`. A link asserts a relationship and the
   surrounding sentence is what says which relationship it is. A trailing
   "Related" or "See also" list says nothing, so do not add one.

3. Write every link bundle-absolute: leading `/`, trailing `.md`, spelled
   exactly as the concept index gives the id. That form survives a file move.

4. Only link to an id in the index. Never invent a path, never link a concept to
   itself, and do not link the same target more than once or twice.

5. Leave links the body already has exactly as they are.

## Hard constraints

- **Change nothing except the links.** Do not add facts, rewrite sentences to
  manufacture a linking opportunity, reorder or retitle sections, or shorten
  anything. Placing a link may need a few words reworded around it; more than
  that is out of scope.
- Keep every footnote reference and every footnote definition untouched.
- A concept with nothing worth linking to comes back unchanged. That is a
  correct answer, not a failure.
"""


def system_prompt(allowed_types: list[str], index: list[tuple[str, str]] | None = None) -> str:
    return f"{SYSTEM}\n{_vocabulary(allowed_types)}\n{_index(index)}"


def outline_system_prompt(allowed_types: list[str]) -> str:
    return f"{OUTLINE_SYSTEM}\n{_vocabulary(allowed_types)}"


def weave_system_prompt(index: list[tuple[str, str]]) -> str:
    return f"{WEAVE_SYSTEM}\n{_index(index)}"


def weave_user_prompt(
    concept_id: str,
    title: str,
    body: str,
    *,
    parent: str | None,
    candidates: list[str],
) -> str:
    """One concept's framing. Kept out of the prefix so the prefix stays cached."""
    lines = [
        f"Rewrite the body of `/{concept_id}.md` ({title!r}) with links woven in.",
        "Do not link it to itself.",
    ]
    if parent is not None:
        lines.append(
            f"This concept is one section of `/{parent}.md`. Link back to the parent "
            f"in prose where it fits naturally."
        )
    if candidates:
        named = ", ".join(f"`/{c}.md`" for c in candidates)
        lines.append(
            f"While writing this concept the author noted it relates to: {named}. "
            f"That is a hint, not an instruction — link them only where the prose "
            f"actually supports it."
        )
    return "\n".join(lines) + f"\n\n--- body ---\n{body}"


def _vocabulary(allowed_types: list[str]) -> str:
    listing = "\n".join(f"- {t}" for t in allowed_types) or "- (none yet)"
    return f"## Type vocabulary\n\n{listing}\n"


def _index(index: list[tuple[str, str]] | None) -> str:
    """The bundle's existing concepts, one line each (§6.4).

    Snapshotted once per run by the caller — anything that moves during a run
    would break the cached prefix.
    """
    if not index:
        return "## Concepts already in this bundle\n\n- (the bundle is empty)\n"
    lines = "\n".join(
        f"- `/{cid}.md` - {description}" if description else f"- `/{cid}.md`"
        for cid, description in index
    )
    return f"## Concepts already in this bundle\n\n{lines}\n"


def user_prompt(source_id: str, source_uri: str, source_title: str, text: str) -> str:
    return (
        f"Source id: {source_id}\n"
        f"Source uri: {source_uri}\n"
        f"Source title: {source_title}\n\n"
        f"--- extracted text ---\n{text}"
    )


def section_user_prompt(
    source_id: str,
    source_uri: str,
    source_title: str,
    text: str,
    *,
    document_title: str,
    document_overview: str,
    section_title: str,
    position: str,
) -> str:
    """One section of an already-planned document.

    The framing lives here rather than in SYSTEM so the cached prefix stays
    byte-identical across whole-document and per-section calls.
    """
    return (
        f"Source id: {source_id}\n"
        f"Source uri: {source_uri}\n"
        f"Source title: {source_title}\n\n"
        f"You are writing ONE concept: the section titled {section_title!r} "
        f"({position}) of the document {document_title!r}.\n\n"
        f"What the document as a whole covers, for context only — do not restate "
        f"it and do not summarise the other sections:\n{document_overview}\n\n"
        f"Below is that section's text and nothing else. It is already the right "
        f"size for one concept, so cover it in full: keep its definitions, named "
        f"authors and their positions, article numbers, cases, examples and "
        f"enumerations. Compressing it is a failure, not a virtue.\n\n"
        f"--- section text ---\n{text}"
    )
