---
name: okf-open-knowledge-format
description: >
  Create, validate, and enrich Open Knowledge Format (OKF) bundles — the open
  spec for representing organizational knowledge as markdown files with YAML
  frontmatter. Use when the user mentions 'OKF', 'Open Knowledge Format',
  'knowledge bundle', 'OKF bundle', 'create a knowledge base for agents',
  'validate OKF', 'convert to OKF', 'enrich knowledge docs', 'agent-readable
  knowledge', 'LLM wiki', 'knowledge catalog', 'kcmd', or wants to structure
  knowledge as markdown files for AI agent consumption. Also use when the user
  has a directory of markdown files and wants to make them interoperable or
  conformant with the OKF standard. Even for simple requests like 'make this
  folder OKF conformant' — the skill has critical structural rules the agent
  needs.
metadata:
  author: ft.ia.br
  version: "1.1"
  date: 2026-06-17
  repository: https://github.com/fabricioctelles/skills
  license: Apache-2.0
  category: library-and-api-reference
  local_patch: "Updated to OKF v0.2 on 2026-08-04; upstream targets v0.1."
  okf_version: "0.2"
---

# Open Knowledge Format (OKF)

OKF is a vendor-neutral, open spec for representing knowledge as a directory of markdown files with YAML frontmatter. No SDK required — if you can `cat` a file, you can read OKF. Created by Sam McVeety & Amir Hormati at Google Cloud; v0.1 announced June 12, 2026.

**Target v0.2.** v0.2 supersedes v0.1. Produce v0.2 unless the user asks otherwise. The two v0.1 forms that were retired are called out inline below; still *read* them without complaint when you encounter an older bundle.

It formalizes the "LLM Wiki" pattern ([Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)) into an interoperable format: wikis written by different producers can be consumed by different agents without translation.

For the full normative spec, see [references/spec-v02.md](references/spec-v02.md) (verbatim copy of `GoogleCloudPlatform/knowledge-catalog/okf/SPEC.md`). [references/spec-v01.md](references/spec-v01.md) is retained only for reading legacy bundles.

### Design Principles

1. **Minimally opinionated** — Only `type` is required. The spec defines interoperability surface, not content model.
2. **Producer/consumer independence** — Who writes and who reads are decoupled. Human-authored bundles feed agents; LLM-generated bundles are browsed by humans.
3. **Format, not platform** — No cloud, SDK, or vendor dependency. Value comes from how many parties speak it.

---

## Key Terminology

- **Bundle** — A directory tree of `.md` files. The unit of distribution (git repo, tarball, or subdirectory).
- **Concept** — One markdown file = one unit of knowledge (table, metric, playbook, API, etc.)
- **Concept ID** — File path within the bundle, minus `.md` suffix. Example: `tables/users.md` → ID `tables/users`
- **Frontmatter** — YAML block between `---` delimiters at file top.
- **Body** — Everything after the frontmatter. Standard markdown.
- **Link** — Standard markdown link expressing a relationship between concepts.
- **Source** — A material a concept derives from, external or internal, recorded in `sources`. (v0.1 called these Citations and kept them in the body.)
- **Credibility signal** — An objective per-source fact (`author`, `usage_count`, `last_modified`). OKF records signals, never a score.
- **Actor** — Identity string: `<producer>/<version>` for agents, `human:<id>` for people, `process:<id>` for automation.
- **Trust tier** — Derived from `verified`: unverified / machine-confirmed / human-reviewed.
- **Attested Computation** — A concept carrying a sanctioned computation plus the means to check a run produced its value that way.

---

## Quick Reference — Frontmatter Fields

| Field | Required? | Description |
|-------|-----------|-------------|
| `type` | **YES** | Kind of concept (free-form string, e.g. `BigQuery Table`, `Metric`, `Playbook`, `API Endpoint`) |
| `title` | Recommended | Human-readable display name |
| `description` | Recommended | One-sentence summary |
| `resource` | Recommended | URI identifying the underlying asset (omit for abstract concepts) |
| `tags` | Optional | YAML list for cross-cutting categorization |
| `sources` | Optional | List of materials the concept derives from. See Provenance below. |
| `generated` | Optional | `{ by: <actor>, at: <ISO 8601> }` — how the current content was produced. `by` required within it. |
| `verified` | Optional | List of `{ by: <actor>, at: <ISO 8601> }` confirmation events. |
| `status` | Optional | `draft` \| `stable` \| `deprecated`. Absent ⇒ `stable`. |
| `stale_after` | Optional | Absolute date `YYYY-MM-DD`. Stale when `today >= stale_after`. |
| `timestamp` | **v0.1 legacy** | Superseded by `generated.at`. Read it as a fallback; never write it. |

Attested Computation concepts add `runtime`, `parameters`, `computation`, `executor`, `attester` — see that section below.

Additional producer-defined keys are allowed. Never reject unknown fields.

## Reserved Filenames

| File | Purpose | Has frontmatter? |
|------|---------|-----------------|
| `index.md` | Directory listing for progressive disclosure | NO* |
| `log.md` | Change history, newest first | NO |

*Exception: bundle-root `index.md` MAY have frontmatter with `okf_version: "0.2"` to declare spec version.

## Conventional Body Headings

| Heading | When to use |
|---------|-------------|
| `# Schema` | Data assets — describe columns/fields |
| `# Examples` | Show concrete usage (code blocks, queries) |
| `# Computation` | The sanctioned computation of an Attested Computation |
| `# Citations` | **v0.1 legacy.** Superseded by `sources` + footnotes. Don't write it. |

---

## Provenance, Trust & Lifecycle (v0.2)

All optional, all in frontmatter. Absence carries meaning — an unverified concept is distinguishable from a verified one, but is never rejected.

### `sources` — where it came from

```yaml
sources:
  - id: ga4-schema
    resource: https://developers.google.com/analytics/bigquery/export-schema
    title: GA4 BigQuery Export schema
    author: team:ga4-docs
    usage_count: 5000
    last_modified: 2026-05-30
usage_window: { from: 2026-06-01, to: 2026-06-30 }
```

- `resource` is **required** within an entry. Either a followable artifact (absolute URL, bundle-relative path, or a path into `references/`) or a scope descriptor it cannot follow (e.g. `all queries in BigQuery project X`).
- `id` is optional but SHOULD be present when the body cites the source.
- Credibility signals, all optional: `author` (authority, actor convention), `usage_count` (adoption/liveness over `usage_window`), `last_modified` (source recency — distinct from `generated.at`, which is when the *concept* was written).
- `usage_window` is written once as a sibling of `sources` and frames every `usage_count`; a single entry MAY override it.
- **Never store a credibility score.** Record signals; consumers infer.
- Read `usage_count` as liveness and trend, not a precise cross-kind ranking.

**Per-claim attribution** uses a markdown footnote whose label *is* a `sources[].id`:

```markdown
The `events_` table is sharded daily as `events_YYYYMMDD`.[^ga4-schema]

[^ga4-schema]: GA4 BigQuery Export schema
```

Labels are keyed, not positional, so reordering `sources` can't silently misattribute. Lineage needs no field: when a `resource` points at another concept, the derivation edge already exists in the link graph.

### `generated` / `verified` — trust

```yaml
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }
verified:
  - { by: human:ahormati, at: 2026-06-25T09:00:00Z }
  - { by: process:finance-nightly, at: 2026-06-26T02:00:00Z }
```

Who *wrote* a concept need not be who *confirmed* it, so these stay separate. A single verifier MAY be a bare `{ by, at }` mapping — consumers MUST treat that as a one-element list.

Trust tiers, lowest to highest: no `verified` ⇒ **unverified**; `verified` by non-`human:` actors only ⇒ **machine-confirmed**; any `human:<id>` verifier ⇒ **human-reviewed**. Advisory signals, not access control.

### Lifecycle

- `status: draft | stable | deprecated`. Absent ⇒ `stable`. Keep `deprecated` concepts for links and history rather than deleting them.
- `stale_after: YYYY-MM-DD` — absolute date, not a relative TTL, so staleness is a plain date comparison.

### Actor convention

`<producer>/<version>` for agents and tools · `human:<id>` for people · `process:<id>` for automated processes. Trust classification keys off the `human:` prefix, so you MUST use it for hand-authored or human-confirmed content.

---

## Attested Computations

`type: Attested Computation` carries a sanctioned way to compute a value so a consumer can confirm the agent ran *that* computation rather than improvising. Provenance answers "where did this claim come from"; attestation answers "was this number produced the way we said it must be." Each computation is its own concept — trust state is per computation, and one computation can back many consumers.

Fields beyond the normal families:

- `runtime` — **required for this type**. Defines what `parameters` mean and how executor/attester interpret the computation. E.g. `bigquery`, `postgres`, `dbt`, `python`, `Looker`.
- `parameters` — list of `{ name, type, required }`; the only holes an agent may fill.
- `computation` — optional path to a file holding the computation. Absent ⇒ the body `# Computation` fence *is* the computation.
- `executor` — `resource` names run instructions or code; `receipt` declares the fields a run must return (e.g. `[job_id, executed_sql, result]`).
- `attester` — `resource` names deterministic, no-LLM code that reads a receipt and returns a verdict, run consumer-side.

```yaml
type: Attested Computation
runtime: bigquery
parameters:
  - { name: year, type: integer, required: true }
executor:
  resource: references/skills/run-on-bq.md
  receipt: [job_id, executed_sql, result]
attester:
  resource: references/attesters/revenue.py
```

**Critical:** the agent MAY only supply *values* for declared `parameters`. It MUST NOT author or edit the computation. Comparison happens on the expanded artifact the receipt carries, so a rewritten query or swapped computation file fails the check.

`verified` and attestation are distinct and both exist: `verified` confirms the *definition* still matches policy (doc-level, stored in the bundle); attestation confirms a single *run* (per-call, runtime, never stored in the bundle).

---

## Create a Bundle

When the user wants to create an OKF bundle from scratch:

### 1. Determine scope and structure

Ask: What knowledge are we capturing? (tables, metrics, APIs, playbooks, etc.)
Organize into a directory tree that makes sense for the domain.

### 2. Create concept documents

Each concept = one `.md` file. Minimal conformant example:

```markdown
---
type: Metric
title: Monthly Recurring Revenue
description: Sum of all active subscription revenue normalized to monthly.
tags: [revenue, saas]
generated: { by: human:jdoe, at: 2026-06-13T10:00:00Z }
status: stable
---

# Monthly Recurring Revenue (MRR)

## Definition

Sum of all active subscriptions normalized to a monthly amount.
Excludes one-time fees and overages.

## Formula

`MRR = Σ(active_subscription_monthly_value)`

## Related

- [Churn Rate](./churn.md) uses MRR as denominator
- [ARR](./arr.md) = MRR × 12
```

For more examples across domains, see [references/examples.md](references/examples.md).

### 3. Cross-link concepts

Use standard markdown links. Two forms:

- **Absolute** (bundle-relative, starts with `/`): `[customers](/tables/customers.md)` — **preferred** (stable when files move)
- **Relative**: `[churn](./churn.md)`

Links assert relationships. The kind of relationship is conveyed by surrounding prose, not by the link syntax. Broken links are explicitly permitted — they represent knowledge not yet written.

### 4. Generate index.md

Place in any directory for progressive disclosure. No frontmatter. Format:

```markdown
# Metrics

- [MRR](./mrr.md) - Monthly recurring revenue
- [Churn](./churn.md) - Monthly churn rate
- [NPS](./nps.md) - Net Promoter Score
```

Entries should include the description from the linked concept's frontmatter.

### 5. Generate log.md (optional)

Chronological change history, newest first, ISO 8601 date headings:

```markdown
# Update Log

## 2026-06-13
- **Creation**: Added MRR, Churn, and NPS metrics.
- **Creation**: Established directory structure.

## 2026-06-10
- **Initialization**: Bundle created.
```

The bold leading word (`**Update**`, `**Creation**`, `**Deprecation**`) is convention, not requirement.

### 6. Declare version (optional)

Bundle-root `index.md` may include frontmatter declaring the spec version:

```markdown
---
okf_version: "0.2"
---

# My Knowledge Bundle

- [Tables](./tables/) - Database tables
- [Metrics](./metrics/) - Business KPIs
```

This is the only place frontmatter is permitted in an `index.md`.

### 7. Distribution

A bundle can be distributed as:
- A **git repository** (recommended — history, attribution, diffs)
- A tarball or zip archive
- A subdirectory within a larger repository

### 8. Verify conformance

Three rules — all must pass (unchanged from v0.1 to v0.2):
1. Every non-reserved `.md` file has parseable YAML frontmatter
2. Every frontmatter has a non-empty `type` field
3. Reserved files (`index.md`, `log.md`) follow their defined structure when present

When the trust, lifecycle, provenance, or computation families are present, follow their rules — but their *absence* is never a conformance failure.

---

## Validate a Bundle

### Preferred: okflint (when available)

[okflint](https://pypi.org/project/okflint/) is a dedicated Python linter for OKF bundles — deterministic, reproducible, no LLM. Requires Python 3.12+. If installed, always prefer it over the built-in bash script.

Three subcommands:
- `okflint audit` — inventory and diagnostics (conformance stats, broken links, split candidates). Always exits 0.
- `okflint validate` — normative gate. Exit `0` = pass, `1` = conformance failure, `2` = bad manifest. Use in pre-commit and CI.
- `okflint index` — generates conformant `index.md` files. Dry-run by default; writes only with `--apply`.

**Agent behavior:** Before validating, check if okflint is installed (`command -v okflint`). If NOT installed, ask the user:

> "okflint (a dedicated OKF linter — manifest-driven profiles, wikilink resolution, JSON output for CI) isn't installed. Want me to install it?
> 1. `uv tool install okflint` (recommended — isolated, no venv needed)
> 2. `pip install okflint`
> 3. Skip it (basic validation with the bundled bash script)"

If the user agrees to install:

```bash
# Option 1: uv (recommended — installs isolated, no venv needed)
uv tool install okflint

# Option 2: pip (installs in current environment)
pip install okflint

# Verify installation
okflint --version
```

After installation (or if already available):

```bash
# Full validation with manifest (if okf-base.yaml exists)
if [ -f okf-base.yaml ]; then
  okflint validate --manifest okf-base.yaml ./bundle/
else
  # Core OKF validation only (no manifest needed)
  okflint validate ./bundle/
fi
```

**okflint advantages over the built-in script:**
- Manifest-driven profiles (enforce custom required fields, status vocabularies, per-type constraints)
- Wikilink resolution against full Obsidian vault
- JSON output (`--json`) for CI pipeline parsing
- Detects broken markdown links and ambiguous wikilinks
- Exit codes: `0` = pass, `1` = conformance failure, `2` = bad manifest

### Fallback: built-in bash script

When okflint is not installed, use [scripts/validate.sh](scripts/validate.sh) which checks the 3 core conformance rules.

When asked to validate, check the 3 conformance rules. Report:

```
✅ PASS: 12/12 concept files have valid frontmatter with type field
✅ PASS: index.md follows list structure (no frontmatter)
✅ PASS: log.md uses ISO 8601 date headings, newest first

⚠  WARNING: 3 files missing 'description' field (recommended)
⚠  WARNING: 2 broken cross-links (permitted but worth noting)
```

For a script-based check, see [scripts/validate.sh](scripts/validate.sh).

### Errors (conformance failures)

- `E1`: File `{path}` has no YAML frontmatter
- `E2`: File `{path}` has frontmatter but no `type` field (or empty)
- `E3`: Reserved file `{path}` has unexpected structure

### Warnings (non-blocking, spec allows these)

- `W1`: Missing recommended field `title` or `description`
- `W2`: Broken cross-link `{link}` in `{file}`
- `W3`: No `generated` field (or `generated` without `by`)
- `W4`: No `index.md` in directory `{dir}`
- `W5`: `log.md` dates not in ISO 8601 format

Consumers MUST NOT reject a bundle because of: missing optional fields, unknown type values, unknown frontmatter keys, broken links, or missing index files.

---

## Enrich Concepts

When the user has existing OKF concepts that need enrichment:

### Add schema section

For data assets, add `# Schema` with a columns table:

```markdown
# Schema

| Column | Type | Description |
|--------|------|-------------|
| `order_id` | STRING | Unique identifier |
| `customer_id` | STRING | FK to [customers](/tables/customers.md) |
```

### Add examples section

For APIs, queries, or tools, add `# Examples` with fenced code blocks showing usage.

### Add provenance

When claims derive from external sources, add a `sources` entry in frontmatter and attribute the specific claim with a footnote keyed to its `id`:

```markdown
---
sources:
  - id: bq-schemas
    resource: https://cloud.google.com/bigquery/docs/schemas
    title: BigQuery schema docs
    last_modified: 2026-05-30
---

Nested and repeated fields are flattened on export.[^bq-schemas]

[^bq-schemas]: BigQuery schema docs
```

`resource` may be an absolute URL, a bundle-relative path, a path into a `references/` subdirectory, or a scope descriptor. Do **not** add a `# Citations` section — that's the retired v0.1 form.

### Add cross-links

Weave links into natural prose. Don't create a standalone "links" section — express relationships in context where they're meaningful.

### Fill recommended fields

If `title`, `description`, `tags`, or `generated` are missing, add them. Derive values from body content when possible. Set `generated.by` to whoever actually produced the content — yourself as `<producer>/<version>`, or `human:<id>` if the user wrote it.

### Enrichment workflow reference

The reference agent follows this pattern — apply the same logic manually:
1. Start with metadata-only docs (just frontmatter + minimal body)
2. Add schema/structure from source system
3. Add `sources` entries from authoritative documentation, with footnotes for specific claims
4. Weave cross-links based on discovered relationships (FKs, shared tags, join paths)
5. Generate `index.md` files for progressive disclosure

---

## Convert Sources to OKF

For detailed conversion guides, see [references/conversion.md](references/conversion.md).

### Quick rules

**Notion export:** Properties → frontmatter. Remove UUID suffixes from filenames. Convert Notion links → relative markdown links.

**Obsidian vault:** Convert `[[wikilinks]]` → `[title](./file.md)`. Ensure `type` field exists. Move inline `#tags` to frontmatter.

**CSV/spreadsheet:** Each row = one concept. Map columns to frontmatter fields. First column = filename.

---

## Guardrails

1. **NEVER invent data.** If you don't know the correct `type`, ask. If you don't have schema info, leave it out. No fabricated URLs or column names.
2. **Preserve unknown fields.** OKF explicitly allows extension. Don't delete fields you don't recognize.
3. **Don't impose taxonomy.** Type values are free-form strings. Suggest descriptive values but never reject a bundle for having unexpected types.
4. **Broken links are OK.** The spec explicitly permits them — they represent not-yet-written knowledge.
5. **Minimal by default.** Generate only `type` (required) + recommended fields that are warranted. Don't pad with empty values.
6. **Ask before assuming.** If the domain is unclear, ask what types and structure make sense.
7. **NEVER write `verified` on someone else's behalf.** `verified` records a confirmation that actually happened. Writing `verified: { by: human:... }` because content looks right forges a human-reviewed trust tier. Record your own authorship in `generated.by`; leave `verified` absent until a real human or process confirms.
8. **NEVER author or edit an Attested Computation's computation.** Supply values for declared `parameters` only.

---

## Serve via Google Cloud Knowledge Catalog

Google Cloud's Knowledge Catalog **natively ingests OKF bundles** and serves them to agents. This is the enterprise path — optional but powerful.

### kcmd CLI (Metadata as Code)

`kcmd` is a bidirectional sync tool between OKF-like local metadata and Knowledge Catalog. Think "git for metadata."

```bash
# Initialize from BigQuery dataset
kcmd init --bigquery-dataset <project>.<dataset>

# Pull current state from catalog
kcmd pull

# Push local changes
kcmd push --dry-run
kcmd push
```

Also ships as an **MCP server** for agent integration:

```json
{
  "mcpServers": {
    "kc-mac": {
      "command": "kcmd",
      "args": ["mcp", "--path", "/path/to/root"]
    }
  }
}
```

MCP tools: `pull`, `push`, `list-entries`, `lookup-entry`, `modify-entry`.

### Reference Agent

The reference agent (Python, Google ADK, Gemini) auto-generates OKF bundles from BigQuery metadata. It is a **proof of concept**, not a supported library — the module is `reference_agent`, invoked as `python -m reference_agent enrich --source bq --dataset <project>.<dataset> --out ./bundles/<name>`. BigQuery is its only source. Two-pass architecture:

1. **BQ pass** — one OKF doc per table/view from metadata
2. **Web pass** — LLM crawls seed URLs and for each page decides to:
   - **(a) Enrich** existing concepts with citations/schemas
   - **(b) Mint** a new `references/<slug>` doc
   - **(c) Skip** irrelevant content

Controls: `--web-seed-file`, `--web-max-pages` (default 100), `--web-max-depth` (default 2), `--web-allowed-host`, `--web-allowed-path-prefix`, `--web-denied-path-substring`, `--no-web`, `--concept <type>/<name>`. It also has a `visualize` subcommand that renders any bundle as a self-contained `viz.html`.

**When to mention this to users:** If they're enriching BigQuery datasets, point them to the [reference agent](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf). If they want enterprise catalog integration, point to [kcmd](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/toolbox/mdcode) and the [ingest demo](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/toolbox/mdcode/demo).

---

## Output Format

When creating a bundle, present results as:

1. **Directory tree** showing the full structure
2. **Each file's content** in fenced code blocks
3. **Conformance check** confirming the bundle passes the 3 rules

```
saas-metrics/
├── index.md
├── log.md
├── mrr.md
├── churn.md
└── nps.md
```

Then show each file, then confirm: "Bundle is OKF v0.2 conformant ✅"
