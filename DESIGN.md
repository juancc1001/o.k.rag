# ragged — Design

**One line:** drop files in, get out a portable OKF v0.2 knowledge bundle, a generated agent contract, and a served graph that shows both the links the knowledge *has* and the ones it *should* have.

**Status: M1 built.** See [§13 Milestones](#13-milestones) for what exists and what comes next. `README.md` covers installation and usage.

## Contents

| § | | § | |
|---|---|---|---|
| [1](#1-goals-and-non-goals) | Goals and non-goals | [8](#8-the-vector-map) | The vector map |
| [2](#2-the-three-layer-model) | The three-layer model | [9](#9-the-source-interface--designed-now-implemented-later) | The `Source` interface |
| [3](#3-repository-layout) | Repository layout | [10](#10-agent-contract) | Agent contract |
| [4](#4-data-model) | Data model | [11](#11-http-api-and-config) | HTTP API and config |
| [5](#5-the-sidecar) | The sidecar | [12](#12-risks-and-open-questions) | Risks and open questions |
| [6](#6-the-pipeline) | The pipeline | [**13**](#13-milestones) | **Milestones** |
| [7](#7-retrieval--out-of-scope) | Retrieval — out of scope | | |

---

## 1. Goals and non-goals

### Goals

1. **Ingest → bundle.** Turn a folder of heterogeneous files (PDF, DOCX, XLSX, PPTX, HTML, MD, TXT) into an OKF v0.2 conformant bundle of markdown concepts with cross-links, `index.md` files, and a `log.md`.
2. **Agent automatizer.** Emit the artifacts an agent needs to *consume* that bundle: a generated `AGENTS.md` (the Karpathy "schema" layer) and an MCP server exposing `search` / `read_concept` / `ingest` / `lint`.
3. **Vector map.** Serve a browser graph where nodes are concepts positioned by embedding similarity, solid edges are real markdown links, and ghost edges are high-similarity pairs with *no* link — the lint signal.
4. **Idempotent re-ingest.** Re-running over a changed file updates its concepts; it never duplicates them.
5. **Pluggable sources.** Phase 1 is local filesystem only, but the `Source` interface is defined from day one so BigQuery and GCS/S3 are plugins, not a rewrite.

### Non-goals (phase 1)

- Multi-user auth, collaboration, or hosted deployment. Single user, localhost.
- A chat UI. The consumer of the bundle is Claude Code / any MCP-capable agent, not a bespoke chatbot.
- Writing `verified` frontmatter. See §4.4 — this is a hard rule, not a deferral.
- Attested Computations. The OKF v0.2 family is supported on *read* (fields preserved), but ragged does not author them.

---

## 2. The three-layer model

Straight from the LLM Wiki pattern, with OKF as the interchange format for layer 2.

| Layer | Directory | Owner | Mutability |
|---|---|---|---|
| 1. Raw sources | `sources/` | The user | Immutable — ragged reads, never writes |
| 2. The bundle | `bundle/` | ragged (LLM-authored) | Rewritten on every ingest |
| 3. The schema | `agent/` | ragged (generated), user-editable | Regenerated on demand |

Plus a fourth thing that is *not* a layer because it is not knowledge:

| Sidecar | `.ragged/` | ragged | Derived, disposable, rebuildable from layers 1+2 |

**The sidecar rule is the load-bearing constraint of this design.** Nothing derived — embeddings, ANN index, 2D projection, content hashes, extraction cache — goes inside `bundle/`. `bundle/` must remain a directory you can `tar` up, hand to someone with no knowledge of ragged, and have them consume with `cat`. That is the entire value proposition of OKF; a `.faiss` file or an `embedding:` frontmatter key destroys it.

---

## 3. Repository layout

```
ragged/
├─ sources/                    # layer 1 — immutable raw input
│  └─ <whatever the user drops here, any nesting>
│
├─ bundle/                     # layer 2 — the OKF v0.2 product
│  ├─ index.md                 # okf_version: "0.2" (the only index.md with frontmatter)
│  ├─ log.md
│  ├─ references/              # web/external material minted as its own concepts
│  └─ <domain>/                # producer-defined tree (see §4.2)
│     ├─ index.md
│     └─ <concept>.md
│
├─ .ragged/                    # sidecar — gitignored, rebuildable
│  ├─ manifest.json            # source hash → concept ids; the idempotency ledger
│  ├─ extracted/               # cached plain-text extractions, keyed by content hash
│  ├─ embeddings.npy           # float32 [n_concepts, 384]
│  ├─ embeddings_meta.json     # row index → concept id, embedded_hash
│  ├─ layout.json              # 2D projection coordinates
│  └─ graph.json               # what the frontend fetches (nodes + both edge sets)
│
├─ agent/                      # layer 3 — the agent contract
│  ├─ AGENTS.md                # generated: how to query this bundle
│  └─ mcp_server.py            # search / read_concept / ingest / lint over stdio
│
├─ src/ragged/
│  ├─ sources/                 # Source implementations (local, later gcs, bq)
│  ├─ extract/                 # bytes → text, per format
│  ├─ synth/                   # text → OKF concepts (Claude)
│  ├─ okf/                     # read, write, validate, index/log generation
│  ├─ vectors/                 # embed, knn, project
│  ├─ api/                     # FastAPI app
│  └─ cli.py
│
├─ web/                        # Vite + React + TS + sigma.js
├─ ragged.toml                 # config (see §11)
└─ pyproject.toml
```

`.gitignore` must contain `.ragged/`. The bundle is committed; the sidecar is not.

---

## 4. Data model

### 4.1 OKF v0.2 conformance

Three rules, all enforced by `okflint validate` in CI:

1. Every non-reserved `.md` file has parseable YAML frontmatter.
2. Every frontmatter block has a non-empty `type`.
3. `index.md` and `log.md` follow their defined structures.

`index.md` files carry no frontmatter except the bundle-root one, which carries exactly `okf_version: "0.2"`.

### 4.2 Frontmatter policy

ragged writes:

| Field | Policy |
|---|---|
| `type` | Required. From the type vocabulary — see below. |
| `title` | Always. Human-readable, derived from the concept, not the filename. |
| `description` | Always. One sentence. Reused verbatim in the parent `index.md` entry. |
| `resource` | When the concept maps to a followable artifact: `file:///…` for a local source, an absolute URL for web material. Omitted for abstract concepts. |
| `tags` | When the synthesis pass produces cross-cutting tags with ≥2 members. Never single-member tags. |
| `sources` | Always ≥1 entry: the file the concept derives from. `resource` required per entry; `id` present so the body can footnote it. `last_modified` from filesystem mtime. |
| `generated` | Always. `{ by: "ragged/<version>", at: <ISO 8601> }`. |
| `verified` | **Never written.** See §4.4. |
| `status` | `draft` on first synthesis; promoted to `stable` only by a human edit. Absent ⇒ `stable` per spec, so we write it explicitly. |
| `stale_after` | Only if the config sets a default TTL for a source; computed to an absolute date at write time. |
| `x_ragged_source_hash` | Extension field. SHA-256 of the source bytes this concept derives from. Producer-defined keys are explicitly allowed; consumers must preserve unknown keys. This is how re-ingest detects staleness without polluting `.ragged/` lookups. |

**Type vocabulary.** `type` is a free-form string and the spec forbids imposing a taxonomy. But a *bundle* benefits from internal consistency, so ragged holds a per-bundle vocabulary in `ragged.toml` (`[types] allowed = [...]`). The synthesis pass is shown the current vocabulary and told to reuse an existing value where one fits, or propose a new one. New proposals are appended to the config and reported in the ingest summary — never silently. Defaults for a document corpus: `Document`, `Concept`, `Entity`, `Process`, `Decision`, `Reference`.

### 4.3 Concept granularity — one source file ≠ one concept

This is the most consequential modeling decision and it is deliberately *not* 1:1.

- A short memo → one concept.
- A 60-page report → a parent concept plus child concepts per substantive section, in a subdirectory named for the parent.
- A spreadsheet → one concept per sheet if sheets are semantically distinct, with a `# Schema` section per sheet.

The synthesis pass decides, constrained by: minimum ~150 words of substance per concept (below that, fold into the parent), maximum ~1200 words (above that, split). Every child concept carries the same `sources` entry as its parent and links back to it in prose.

### 4.4 The `verified` rule

ragged writes `generated.by: ragged/<version>` and nothing else in the trust family. It **never** writes `verified`, because `verified` records a confirmation event that actually happened, and writing `verified: {by: human:...}` because content looks correct forges the human-reviewed trust tier. A human promotes a concept by editing the file (or via a future `ragged verify` command that takes an explicit `--as human:<id>`); the pipeline never does it on their behalf.

Corollary: the re-ingest pass must **preserve** a `verified` block a human added, and must flag (not silently drop) when it rewrites the body of a concept that carries one. See §6.4.

---

## 5. The sidecar

### `manifest.json`

The idempotency ledger. Everything about re-ingest correctness lives here.

```json
{
  "version": 1,
  "bundle_root": "bundle",
  "sources": {
    "sources/q3/report.pdf": {
      "content_sha256": "9f2c…",
      "size_bytes": 481203,
      "mtime": "2026-07-14T09:12:04Z",
      "extracted_at": "2026-08-04T11:02:31Z",
      "concepts": ["q3-report", "q3-report/methodology", "q3-report/findings"],
      "last_ingest": "2026-08-04T11:04:52Z"
    }
  },
  "concepts": {
    "q3-report/findings": {
      "source": "sources/q3/report.pdf",
      "body_sha256": "1a77…",
      "human_edited": false
    }
  }
}
```

`body_sha256` is the hash of the concept body *as ragged last wrote it*. If the file on disk hashes differently, a human edited it — set `human_edited: true` and treat the concept as protected (§6.4).

### `embeddings.npy` + `embeddings_meta.json`

`float32[n, 384]`, L2-normalized at write time so cosine similarity is a dot product. Meta maps row → `{concept_id, embedded_body_sha256}`; a concept whose body hash differs from `embedded_body_sha256` is re-embedded.

### `graph.json`

Precomputed server-side, fetched whole by the frontend. See §8.

---

## 6. The pipeline

```
discover → extract → segment → synthesize → link-weave → emit → validate → embed → project → serve
              │                     │            │                  │          │
           (cached)             (Claude)     (Claude)          (okflint)   (local ONNX)
```

Stages 1–7 produce the bundle. Stages 8–9 produce the sidecar vectors. They are separable: `ragged ingest` runs 1–7, `ragged index` runs 8–9, `ragged serve` runs the API + web.

### 6.1 discover

Walk the `Source` (§9) and yield `(uri, bytes_or_stream, mtime)`. Compute `content_sha256`. Diff against `manifest.json`:

- Hash unchanged → **skip entirely**, including extraction and synthesis. No Claude call, no cost.
- Hash changed → mark for re-ingest.
- In manifest but no longer present at source → mark its concepts for `status: deprecated` (per spec: keep deprecated concepts for links and history rather than deleting them). Deletion requires explicit `ragged prune`.

### 6.2 extract

Bytes → plain text plus structural hints (headings, tables, sheet names). Keyed cache in `.ragged/extracted/<sha256>.json` so re-runs after a synthesis-only change cost nothing.

Default extractor: **markitdown** — one dependency covering PDF/DOCX/XLSX/PPTX/HTML/CSV, and it emits markdown, which means the structure survives into the synthesis prompt instead of being flattened. Fallback for PDFs where layout matters (multi-column, heavy tables): **pymupdf** with a layout-preserving mode, selected per-file by config glob.

Extraction failures do not abort the run. They are recorded in the manifest with an `error` field and surfaced in the ingest summary. A corpus of 400 files where 3 are encrypted PDFs should still produce a bundle of 397 files' worth of concepts.

### 6.3 segment

Split extracted text into synthesis units respecting the granularity policy (§4.3), on heading boundaries where they exist and on a semantic-paragraph fallback where they don't. Purely mechanical — no model call. Produces a tree: one root unit per source file, zero or more children.

### 6.4 synthesize (the model)

One call per synthesis unit, using **native schema enforcement** so the result is guaranteed to fit the shape rather than parsed out of prose.

**The provider is pluggable.** `synth/providers/` holds one thin module per backend (`claude.py`, `gemini.py`), each owning authentication, request shape, and decline detection. Everything shared — prompt assembly, the schema, JSON parsing, footnote repair — lives in `synth/__init__.py` so the paths cannot drift. Selected by `[synth].provider`.

| | Anthropic | Gemini |
|---|---|---|
| Schema enforcement | `output_config.format` (`json_schema`) | `response_json_schema` + `response_mime_type` |
| System prompt | cached `system` block | `config.system_instruction` |
| Prompt caching | explicit `cache_control` breakpoint | implicit; reported via `cached_content_token_count` |
| Reasoning depth | `output_config.effort` | no equivalent — left at the model default |
| Decline signal | `stop_reason: "refusal"` + `stop_details` | `finish_reason` in a safety set, or `prompt_feedback.block_reason` |

Both map a decline to `Refused` and a token-cap stop to `SynthError`, so the pipeline treats them identically: a per-file error in the summary, never an aborted run.

A credential is checked **before** discovery so a missing key fails immediately rather than after extracting a whole corpus.

```python
CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "concept_id": {"type": "string"},
        "type": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "body_markdown": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_id": {"type": "string"},
                },
                "required": ["text", "source_id"],
                "additionalProperties": False,
            },
        },
        "candidate_links": {
            "type": "array",
            "items": {"type": "string"},
            "description": "concept_ids this concept likely relates to",
        },
    },
    "required": ["concept_id", "type", "title", "description", "body_markdown"],
    "additionalProperties": False,
}

response = client.messages.create(
    model="claude-opus-5",
    max_tokens=16000,
    output_config={"format": {"type": "json_schema", "schema": CONCEPT_SCHEMA},
                   "effort": "high"},
    system=[{"type": "text", "text": SYNTH_SYSTEM, "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": unit_text}],
)
```

`SYNTH_SYSTEM` is a frozen prefix: the OKF v0.2 authoring rules, the current type vocabulary, the bundle's existing concept index (id + description, one line each), and the body conventions. It is byte-identical across every unit in a run, so it caches — one write, N−1 reads at ~0.1×. Opus 5's cacheable minimum is 512 tokens, which this comfortably clears. **The volatile part (the unit text) goes after the breakpoint.** Any drift in the system prefix — a timestamp, a per-file note — silently destroys this; the prefix builder must be pure.

For bulk ingests (>50 units), route through the **Batches API** instead: 50% cost reduction, and a 400-file corpus is not latency-sensitive. Same schema, same cached prefix.

**Protected concepts.** If `manifest.concepts[id].human_edited` is true, or the on-disk file carries a `verified` block, synthesis does not overwrite it. Instead it writes the proposed replacement to `.ragged/proposed/<id>.md` and reports it. The human resolves. This is the one place where "the LLM owns layer 2" yields to "the human owns their verification".

### 6.5 link-weave (Claude)

A second pass, after all concepts for the run exist. Sees the full concept index and the `candidate_links` from stage 6.4, and rewrites the *bodies* to weave links into natural prose. Per the spec: links assert relationships, and the relationship kind is conveyed by surrounding prose, not syntax — so a standalone "Related" section is wrong and this pass must not create one.

Links are written **bundle-absolute** (`/tables/customers.md`), which is the preferred form and survives file moves.

Broken links are permitted and expected: a concept can reference knowledge not yet written. `okflint` reports them as W2 warnings, not errors.

### 6.6 emit

Write concept files, then generate every `index.md` bottom-up (entries carry the linked concept's `description` verbatim), then append to `log.md` under today's ISO date, newest first. Log entries use the conventional bold leading word: `**Creation**`, `**Update**`, `**Deprecation**`.

Writes are atomic per file (temp + rename) and the whole emit is transactional at the manifest level: the manifest is updated only after every file lands, so a crash mid-emit leaves the previous manifest and the next run reconciles.

### 6.7 validate

Two layers, because neither alone is sufficient:

1. **Built-in checks** — the three conformance rules plus W1–W5 warnings. Always run, no dependencies. This is the floor.
2. **okflint** (pinned, verified against 0.3.1) — the normative gate. `okflint validate --manifest okf-base.yaml <bundle>`; exit 0 required.

**ragged generates `okf-base.yaml`** at the project root, because okflint's `validate` always resolves a manifest (default `okf-base.yaml`) and exits 2 — "bad manifest" — without one. It cannot be run manifest-free. The generated manifest is minimal:

- `base.roots` — non-empty list of `{path, exclude_patterns}`, required.
- `base.reserved_files` — must contain both `index` and `log` keys, required.
- `hygiene.broken_links: warn` — the spec explicitly permits broken links, so they must never fail the gate.
- `hygiene.unknown_fields: off` — otherwise `x_ragged_source_hash` is flagged, and producer-defined keys are spec-legal.
- **No `profile` block.** A profile pins required fields and controlled vocabularies per type; that is the bundle owner's policy call, and enforcing one would reject the new types synthesis proposes (§4.2). Add it once a vocabulary settles.
- **No `okf_version` key.** okflint 0.3.1 recognises only `0.1` and prints a warning for anything else. The bundle-root `index.md` is where the bundle declares `0.2`.

When okflint is absent, or the manifest is missing, validation reports *why* it was skipped rather than silently degrading to the floor.

`okflint audit` takes `--bundle`, not a positional path, and is a separate diagnostic (split candidates, inventory) rather than part of the gate. `okflint index --apply` is **not** used — ragged generates its own `index.md` files in 6.6 with descriptions pulled from frontmatter; `okflint index` stays available as a repair tool for hand-edited bundles.

The okflint subprocess must decode as UTF-8 explicitly — `text=True` inherits the locale codepage and okflint emits characters outside cp1252. That is one instance of a project-wide rule: **never trust a platform default at an I/O boundary.** The full policy, and the AST test that enforces it, are in `README.md` → Encoding policy.

### 6.8 embed

fastembed + `bge-small-en-v1.5`, 384-dim, ONNX, in-process, offline. Embed at **concept granularity** — one vector per concept file, over `title + description + body`. Concepts longer than the model's window are embedded as mean-pooled chunks; the chunk vectors are discarded, only the concept vector is kept.

Chunk-level vectors are deliberately not stored in phase 1. The retrieval path is progressive disclosure (§7), not chunk RAG; adding a chunk index before there is evidence retrieval quality demands it is building the thing this design argues against.

Only concepts whose `body_sha256` changed are re-embedded.

### 6.9 project

Embeddings → 2D. **UMAP** (`umap-learn`) with a fixed `random_state` so the layout is stable across runs — a graph that reshuffles on every ingest is unusable. Falls back to PCA when `n_concepts < 15` (UMAP is unstable at that size) or when `umap-learn` is unavailable (it pulls numba, which is a heavy dependency; it is an optional extra).

kNN is brute-force cosine over the normalized matrix — `n × 384` float32 is 1.5 MB at 1000 concepts, 76 MB at 50k. NumPy handles this in milliseconds and the code is twenty lines. Swap to `hnswlib` only if a real corpus crosses ~100k concepts.

---

## 7. Retrieval — out of scope

Retrieval strategy is deliberately **not** decided in this document. The objective is producing conformant OKF bundles; how an agent later queries one is a separate question, settled empirically against a real corpus rather than argued up front.

What this section fixes is only the constraint that keeps the question open: retrieval lives entirely in `.ragged/`, so it can change without touching `bundle/`. Embeddings in §8 exist to serve the graph (layout + lint), not to commit to a retrieval design.

---

## 8. The vector map

### Two graphs, one view

| Layer | Source | Semantics |
|---|---|---|
| **Link graph** | Markdown cross-links in concept bodies | Explicit, authored, high-precision. Sparse. |
| **Semantic graph** | kNN over concept embeddings | Implicit, computed, high-recall. Dense. |

Rendering only the first gives you what the Google reference agent's `visualize` already does. Rendering only the second gives you a t-SNE blob. The value is the **difference between them**.

### `graph.json`

```json
{
  "nodes": [
    {"id": "q3-report/findings", "title": "…", "type": "Document",
     "status": "draft", "trust": "unverified", "stale": false,
     "x": 0.42, "y": -1.13, "degree": 3}
  ],
  "links": [
    {"source": "q3-report/findings", "target": "metrics/mrr", "kind": "explicit"}
  ],
  "ghosts": [
    {"source": "q3-report/findings", "target": "metrics/churn",
     "kind": "semantic", "similarity": 0.87}
  ],
  "stats": {"concepts": 214, "links": 361, "ghosts": 47, "broken_links": 9}
}
```

**Ghost edge definition:** pair `(a, b)` such that `cos(a, b) ≥ τ` (default `τ = 0.82`, configurable), `b` is in `a`'s top-k neighbours (default `k = 8`), and there is **no** markdown link between them in either direction. Ghosts are computed only on the upper triangle to avoid duplicates.

τ needs calibration against a real corpus. A too-low threshold buries the signal in noise (everything in a coherent bundle is somewhat similar to everything else); a too-high one finds nothing. The UI exposes τ as a live slider, and the config default is set from whatever the slider converges on in practice.

### Visual encoding

- **Position** — UMAP projection of the embedding. Proximity means semantic similarity.
- **Solid edge** — an authored link. This is the knowledge structure as it exists.
- **Ghost edge** (dashed, low opacity, toggleable, off by default) — semantic proximity with no authored link. **This is the work queue.**
- **Node colour** — `type`.
- **Node border** — trust tier: none (unverified) / thin (machine-confirmed) / solid (human-reviewed). Derived from `verified` per the spec's tier rules.
- **Node size** — link degree.
- **Node opacity** — reduced when `status: deprecated` or `today >= stale_after`.

### Stack

**sigma.js v3 + graphology**, WebGL. d3-force is nicer to style but degrades past ~1–2k nodes, and a document corpus of any size will exceed that. Positions come precomputed from UMAP, so no force simulation runs in the browser — sigma is used as a renderer, not a layout engine. Layout is therefore deterministic and instant.

Frontend fetches `graph.json` once and does all filtering client-side. Selecting a node fetches `GET /api/concept/{id}` for the rendered markdown in a side panel.

---

## 9. The `Source` interface — designed now, implemented later

Two producer *families*, and conflating them is the mistake to avoid:

- **Document producers** — unstructured bytes → extraction → LLM synthesis. Local FS, GCS, S3, Drive.
- **Asset producers** — structured metadata → deterministic concept skeleton, no LLM needed for the base doc; optional LLM enrichment pass on top. BigQuery `INFORMATION_SCHEMA`, dbt manifests, OpenAPI specs.

They share an interface and diverge after `discover`:

```python
class Source(Protocol):
    name: str
    family: Literal["document", "asset"]

    def discover(self) -> Iterator[SourceItem]:
        """Yield items with a stable uri and a content hash."""

    def fetch(self, item: SourceItem) -> bytes | Mapping[str, Any]:
        """Document family returns bytes; asset family returns structured metadata."""
```

Phase 1 ships `LocalFileSource` only. `BigQuerySource` and `BlobSource` are stubs with tests asserting the protocol shape, so phase 2 is a plugin.

BigQuery specifically: `INFORMATION_SCHEMA.COLUMNS` + `TABLES` + table descriptions produce a deterministic `type: BigQuery Table` concept with a `# Schema` table and `resource: bq://project.dataset.table`, plus foreign-key-shaped cross-links inferred from column naming. That skeleton needs no model call. The web-enrichment pass (the second half of what the Google reference agent does) is a separate optional stage.

---

## 10. Agent contract

### `agent/AGENTS.md` (generated)

The layer-3 schema doc, regenerated on each ingest. Contains: the bundle's directory structure, the current type vocabulary with counts, the three operations (ingest / query / lint) and how to invoke each, the trust-tier convention, and an explicit instruction that the query path is *read `index.md` first, then read whole concept files* — not "search for chunks".

### `agent/mcp_server.py`

stdio MCP server. Four tools:

| Tool | Signature | Notes |
|---|---|---|
| `search` | `(query: str, k: int = 8) -> list[{concept_id, title, description, score}]` | Embedding kNN. Returns pointers, never bodies — the agent decides what to read. |
| `read_concept` | `(concept_id: str) -> str` | Raw file contents, frontmatter included. |
| `ingest` | `(paths: list[str] \| None) -> IngestSummary` | Runs the pipeline. `None` = full sweep. |
| `lint` | `() -> LintReport` | okflint results + ghost edges + stale concepts + orphans. |

Registering it is a `.mcp.json` entry pointing at the script — which makes any MCP-capable agent, Claude Code included, a consumer of the bundle with no ragged-specific code.

---

## 11. HTTP API and config

### API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/graph` | `graph.json` |
| `GET` | `/api/concept/{id}` | Frontmatter + rendered body |
| `GET` | `/api/search?q=&k=` | kNN, same as the MCP tool |
| `POST` | `/api/ingest` | Kick off a run; returns a job id |
| `GET` | `/api/ingest/{job}` | SSE progress stream |
| `GET` | `/api/lint` | Lint report |

### `ragged.toml`

```toml
[bundle]
root = "bundle"
okf_version = "0.2"
actor = "ragged/0.1.0"

[sources.local]
family = "document"
path = "sources"
include = ["**/*.pdf", "**/*.docx", "**/*.md", "**/*.xlsx", "**/*.pptx", "**/*.html"]
exclude = ["**/~$*", "**/.DS_Store"]

[extract]
default = "markitdown"
overrides = [{ glob = "**/scanned/*.pdf", extractor = "pymupdf-layout" }]

[synth]
provider = "gemini"         # gemini | anthropic
# model = "gemini-2.5-pro"  # omit for the provider default
effort = "high"             # anthropic only
batch_threshold = 50        # route to Batches API above this many units (anthropic)
min_words = 150
max_words = 1200

[types]
allowed = ["Document", "Concept", "Entity", "Process", "Decision", "Reference"]
allow_new = true            # new types are appended and reported, never silent

[vectors]
model = "BAAI/bge-small-en-v1.5"
dim = 384
knn_k = 8
ghost_threshold = 0.82

[projection]
method = "umap"             # umap | pca
random_state = 42
```

---

## 12. Risks and open questions

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Ghost-edge threshold is uncalibrated.** τ = 0.82 is a guess. A coherent corpus may make everything ≥0.9-similar, drowning the signal. | Live slider in the UI; calibrate on the first real corpus; consider per-type thresholds or rank-based (top-k only) instead of absolute similarity. |
| 2 | **Extraction quality caps everything downstream.** Garbage text → garbage concepts, and no amount of prompt work fixes it. | Cache extractions and expose them; a `ragged extract --inspect <file>` command so extraction can be debugged independently of synthesis. |
| 3 | **Synthesis cost on large corpora.** 400 documents × ~3 units × Opus 5 at high effort is real money. | Cached system prefix (~0.1× on the bulk of input tokens), Batches API (50%), hash-based skip on unchanged files. Report estimated cost before a run and require confirmation above a configurable ceiling. |
| 4 | **Local embeddings are weaker than hosted.** bge-small is good, not great, especially on domain jargon. | The embedding provider is behind an interface; swapping to Voyage or Vertex is a config change plus a full re-embed. Since embeddings are sidecar-only, a re-embed touches nothing in the bundle. |
| 5 | **Layout instability across ingests.** UMAP with new points added can reshuffle globally even with a fixed seed. | Fixed `random_state`; investigate `umap.transform()` on a persisted fitted model to place new points into an existing layout rather than refitting. Refit only on explicit `ragged reindex --full`. |
| 6 | **Human edits vs. LLM ownership.** The wiki pattern says the LLM owns layer 2, but a human who verified a concept has a legitimate claim on it. | `human_edited` flag + `verified` detection + `.ragged/proposed/` (§6.4). Never silently overwrite. |
| 7 | **Sensitive content.** Local phase 1 is fine, but document text goes to the Claude API during synthesis, and phase 2 adds cloud sources. | Document this explicitly before first run. Embeddings stay local by default specifically so the *bulk* re-processing path is offline. Revisit before any cloud connector ships. |
| 8 | **okflint version drift.** It's a young package; its manifest profile format may change. | Pin the version in `pyproject.toml`; keep the built-in three-rule fallback working as a hard floor. |

**Open questions for the user:**

- **Corpus profile.** What are the first real files? Format mix and typical length change the segmentation defaults and the extraction fallback rules more than any other input.
- **Domain.** The type vocabulary defaults are generic. If the corpus is (say) legal contracts or engineering RFCs, a domain vocabulary set up front will produce a far better bundle than one discovered file-by-file.
- **Is the bundle committed to git?** Recommended (the spec calls git the preferred distribution form, and it gives you free history/attribution/diffs on the knowledge itself) — but it means document content lands in a repo, which is a decision, not a default.

---

## 13. Milestones

| | Milestone | Status |
|---|---|---|
| **M1** | Walking skeleton | **done** |
| **M2** | Real bundle | next |
| **M4** | Agent contract | after M2 |
| **M3** | Vectors + graph | after M4 |
| **M5** | Connectors | phase 2 |

**M4 and M3 are swapped from the original order.** The objective is that any model *consumes* the bundle, and that is served by the agent contract, not by the graph. The graph is diagnostic; MCP is the product.

---

**M1 — walking skeleton. Done.** `LocalFileSource` + markitdown + single-unit synthesis + emit + okflint + a generated `okf-base.yaml`. Pluggable providers (Anthropic, Gemini). One file in, one conformant concept out. 53 tests. No links, no segmentation, no vectors.

**M2 — real bundle. Next.** In dependency order:

1. **`manifest.json` + idempotent re-ingest.** Highest value: today every `build` re-synthesizes everything and re-pays every model call. Content-hash skip, `status: deprecated` for vanished sources, `human_edited` protection (§6.4).
2. **Segmentation** (§4.3). Today one concept per file regardless of length, so a long PDF becomes one oversized concept.
3. **Link-weave** (§6.5) — without it the bundle is a flat set, not a graph.
4. **Per-directory `index.md`** — progressive disclosure, and it clears the `W4` warnings.

Exit criterion: ingest a real 20-file corpus, validate clean, re-run and observe zero model calls.

**M4 — agent contract.** Generated `AGENTS.md`, MCP server, `.mcp.json`. Three of the four MCP tools (`search`, `read_concept`, `lint`) are credential-free; only `ingest` inherits the write path's requirement, and the generated `AGENTS.md` must say so. Exit criterion: a session with no credential registers the server, answers a real question from the bundle, and reports lint findings.

**M3 — vectors + graph.** Embed, project, `graph.json`, FastAPI, sigma.js frontend with both edge layers and the τ slider. Where the ghost-edge idea gets its first real test. Note `search` works on index traversal before this lands; embeddings upgrade it rather than enable it.

**M5 — connectors.** `BigQuerySource` (asset family) and `BlobSource` (document family).
