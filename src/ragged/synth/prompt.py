"""The synthesis system prompt.

This string is the cached prefix: it MUST be byte-identical across every unit in
a run or prompt caching silently stops working. Nothing dynamic goes in here
except the type vocabulary, which is stable for the run.
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


def system_prompt(allowed_types: list[str]) -> str:
    vocabulary = "\n".join(f"- {t}" for t in allowed_types) or "- (none yet)"
    return f"{SYSTEM}\n## Type vocabulary\n\n{vocabulary}\n"


def user_prompt(source_id: str, source_uri: str, source_title: str, text: str) -> str:
    return (
        f"Source id: {source_id}\n"
        f"Source uri: {source_uri}\n"
        f"Source title: {source_title}\n\n"
        f"--- extracted text ---\n{text}"
    )
