# ragged — Design

**One line:** drop files in, get out a portable OKF v0.2 knowledge bundle, a generated agent contract, and a served graph that shows both the links the knowledge *has* and the ones it *should* have.

**Status: M2 built.** See [§13 Milestones](#13-milestones) for what exists and what comes next. `README.md` covers installation and usage.

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
│  ├─ proposed/                # replacements for concepts a human owns (§6.4)
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
│  ├─ ledger.py                # the idempotency ledger (.ragged/manifest.json)
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
| `x_ragged_source_hash` | Extension field. SHA-256 of the source bytes this concept derives from. Producer-defined keys are explicitly allowed; consumers must preserve unknown keys. Re-ingest reads the ledger, not this — the field is here so a bundle handed to someone who has never heard of ragged still records which revision of the source it came from. |

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

The idempotency ledger, written by `ledger.py`. Everything about re-ingest correctness lives here. (Not `okf/manifest.py`, which generates `okf-base.yaml` for okflint — different artifact, different consumer.)

```json
{
  "version": 1,
  "bundle_root": "bundle",
  "sources": {
    "sources/q3/report.pdf": {
      "content_sha256": "9f2c…",
      "size_bytes": 481203,
      "mtime": "2026-07-14T09:12:04Z",
      "concepts": ["q3-report", "q3-report/methodology", "q3-report/findings"],
      "last_ingest": "2026-08-04T11:04:52Z",
      "complete": true
    }
  },
  "concepts": {
    "q3-report/findings": {
      "source": "sources/q3/report.pdf",
      "body_sha256": "1a77…"
    }
  }
}
```

`concepts` is ordered parent-first, so a skipped source can be reported without re-reading its tree. Keys are sorted on write, so a run that changed nothing rewrites a byte-identical file.

`body_sha256` is the hash of the concept body *as ragged last wrote it*. A file on disk that hashes differently was edited by a human, and is protected (§6.4). This is **computed at read time, not stored as a flag**: a stored `human_edited: true` goes stale the moment the human reverts their edit, and a ledger that has to be told the truth twice will eventually be told it once.

Losing the ledger costs a full re-ingest, never correctness — so every read path degrades to "no record" rather than raising, and a skip additionally requires the recorded concept files to still be on disk.

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

- Hash unchanged → **skip entirely**, including extraction and synthesis. No model call, no cost.
- Hash changed → mark for re-ingest.
- Hash unchanged but a section of it failed last run → re-ingest anyway. The ledger records `complete: false`, because a section lost to a 429 is source content missing from the bundle, and a source recorded as finished is never retried until its bytes happen to change. A failed *weave* does not count: the concept exists and is valid, only its links are missing (§6.5).
- In manifest but no longer present at source → mark its concepts for `status: deprecated` (per spec: keep deprecated concepts for links and history rather than deleting them). Deletion requires explicit `ragged prune`. Marked once: the source record is then dropped, so later runs do not re-announce it.
- Re-ingested, but the new run produced a differently-named tree → **delete** what the old one produced and this one did not. This is the opposite call from the bullet above, and the difference is who owns the name: a vanished source's concepts are knowledge whose input went away, while a renamed tree is the same knowledge under a dead id. Concept ids derive from model-written titles, so a rebuild renames routinely; without this sweep every run leaves a full copy of the previous one behind. A directory left holding only its own `index.md` goes too.

Ids the previous run wrote stay reserved for their own source across the whole run, released only when that source is itself re-ingested. Otherwise a rebuilt source can claim the id of a source that is about to be skipped.

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

The concept index is therefore **snapshotted once, before discovery**, and describes the bundle as it was when the run started. Letting it grow as concepts land would be more informative and would stop the prefix caching after the first unit. The concepts written during the run are seen by link-weave (§6.5) instead, which is the pass that needs them.

For bulk ingests (>50 units), route through the **Batches API** instead: 50% cost reduction, and a 400-file corpus is not latency-sensitive. Same schema, same cached prefix.

**Protected concepts.** If the on-disk body no longer hashes to `manifest.concepts[id].body_sha256`, or the file carries a `verified` block, synthesis does not overwrite it. Instead it writes the proposed replacement to `.ragged/proposed/<id>.md` and reports it. The human resolves. This is the one place where "the LLM owns layer 2" yields to "the human owns their verification".

With no ledger record at all — a wiped sidecar beside a surviving bundle — only `verified` protects. "We cannot tell" must not mean "never rebuild anything again".

A protected concept is still counted as its source's output, so the stale-output sweep below does not delete it.

### 6.5 link-weave (the model)

A second pass, after all concepts for the run exist. Sees the full concept index and the `candidate_links` from stage 6.4, and rewrites the *bodies* to weave links into natural prose. Per the spec: links assert relationships, and the relationship kind is conveyed by surrounding prose, not syntax — so a standalone "Related" section is wrong and this pass must not create one.

The schema returns `body_markdown` and nothing else, so a pass that wanders cannot change a title, a type or a source. Everything except the body was settled by 6.4.

Links are written **bundle-absolute** (`/tables/customers.md`), which is the preferred form and survives file moves.

**Only what this run wrote is woven.** A skipped source's concepts are unchanged output, and rewriting them would spend a model call producing a file nobody asked for. The consequence is that links point forward from new concepts to old ones and not back, until the old one is itself re-ingested — the missing direction is exactly what §8's ghost edges are for.

A concept a human owns is not woven, for the same reason it is not overwritten (§6.4). A weave failure leaves the unlinked concept in place and is reported: linking is an upgrade to a concept, not a precondition for one — which is also why, unlike a failed *section* (§6.1), it does not mark the source incomplete. Re-synthesizing a whole document to retry two links would cost more than the links are worth.

Broken links are permitted and expected: a *human* can reference knowledge not yet written, and `okflint` reports that as a W2 warning rather than an error. A model handed the full concept index is in a different position — a path outside that index is a guess, not a forward reference — so the pass unwraps such links back to plain text and reports the targets. Both halves matter: the tolerance is for authors, the repair is for generators.

Two further repairs, mechanical because the intent is unambiguous, both found on the first real corpus rather than reasoned about in advance:

- **Inverted syntax** — `[/finance/pricing.md](list prices)`. Swapped back when the label side resolves to a concept and the target side does not.
- **A path where the label should be** — `[/finance/pricing.md](/finance/pricing.md)`. The link works, which is why no linter objects, but a raw path is not a label; it becomes the target concept's title. This is why the weave index carries titles as well as descriptions, unlike the synthesis one.

Prompt wording is not the fix for any of these. A repair that runs after the response is checkable and testable offline; a rule in the prompt is neither.

**The ledger must record the post-weave `body_sha256`** (§5). Recording the pre-weave hash would make the next run read every concept as hand-edited and refuse to touch any of them.

### 6.6 emit

Write concept files, then generate every `index.md` bottom-up, then append to `log.md` under today's ISO date, newest first. Log entries use the conventional bold leading word: `**Creation**`, `**Update**`, `**Deprecation**`.

**Indexes are read off the bundle, not accumulated during the run.** A directory whose concepts were all skipped has to be indexed exactly like one just written, and an entry must never disagree with the concept it points at — so every label is the target's own `title` and every line its own `description`, verbatim. Deriving a label from the slug instead folds accents away and then applies English title-case, which reads as damage in any other language.

An entry for a concept that owns a subdirectory states how many concepts are below it and links to that subdirectory's index. Without it the root index is a dead end: a reader concludes the bundle holds three documents when it holds eighteen. A directory no concept owns is listed in its own right. A `status: deprecated` concept is marked as such, since an index that lists it as an equal is the one place the deprecation would be invisible.

The root index's heading comes from `[bundle].title`, falling back to the project directory name — which names the tool rather than the knowledge unless the two happen to agree.

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

**Nothing in the build path uses embeddings.** They exist for §8 — graph layout and the missing-link lint — so they are needed at M3, which sits behind M4. Concepts are produced, validated and consumed without them.

**Local vs hosted is an open decision, to be made at M3.** The original argument for local (fastembed + `bge-small-en-v1.5`, 384-dim, ONNX, offline) rested partly on the Anthropic API having no embedding model. That reason expired when Gemini became the default provider — it has one, reachable with the key the build already needs. What survives is weaker: rebuilds are free and offline, and embedding a *query* at read time needs no credential. Against local: ~150 MB of `onnxruntime` dependencies for something the existing key already covers.

Note the asymmetry that makes this low-stakes: concept vectors are computed at **build** time, when a credential is present either way, and then stored. Reading them needs no credential regardless of who produced them. Only query embedding would, and `search` falls back to index traversal.

Decide it by measurement: if local `bge-small` produces useful ghost edges on the real corpus, keep it. If the signal is noise, compare against `gemini-embedding-001`. Either way the vectors live in `.ragged/` — swapping providers is deleting `embeddings.npy` and re-embedding, with zero impact on `bundle/`. This is the sidecar rule paying for itself.

Whichever wins: embed at **concept granularity** — one vector per concept file, over `title + description + body`. Concepts longer than the model's window are embedded as mean-pooled chunks; the chunk vectors are discarded, only the concept vector is kept.

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
# title = "Derecho Informático"   # omit -> the project directory name

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
| 4 | **Embedding provider is undecided** (§6.8). Local `bge-small` is weaker on domain jargon; hosted costs money and, for query embedding, a credential at read time. | Decide at M3 by measuring ghost-edge quality on the real corpus, not up front. Sidecar-only, so swapping is a re-embed that touches nothing in `bundle/`. |
| 5 | **Layout instability across ingests.** UMAP with new points added can reshuffle globally even with a fixed seed. | Fixed `random_state`; investigate `umap.transform()` on a persisted fitted model to place new points into an existing layout rather than refitting. Refit only on explicit `ragged reindex --full`. |
| 6 | **Human edits vs. LLM ownership.** The wiki pattern says the LLM owns layer 2, but a human who verified a concept has a legitimate claim on it. | Body-hash comparison + `verified` detection + `.ragged/proposed/` (§6.4). Never silently overwrite. Built in M2. |
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
| **M2** | Real bundle | **done** |
| **M4** | Agent contract | next |
| **M3** | Vectors + graph | after M4 |
| **M5** | Connectors | phase 2 |
| **M6** | Extract `okf/` as a standalone library | gated — see below |

**M4 and M3 are swapped from the original order.** The objective is that any model *consumes* the bundle, and that is served by the agent contract, not by the graph. The graph is diagnostic; MCP is the product.

---

**M1 — walking skeleton. Done.** `LocalFileSource` + markitdown + single-unit synthesis + emit + okflint + a generated `okf-base.yaml`. Pluggable providers (Anthropic, Gemini). One file in, one conformant concept out. 53 tests. No links, no segmentation, no vectors.

**M2 — real bundle. All four items done.** In dependency order:

1. **`manifest.json` + idempotent re-ingest.** **Done**, in `ledger.py` (`okf/manifest.py` was taken by the okflint manifest). Content-hash skip so an unchanged source costs nothing — not extraction, not the N+1 model calls segmentation turned one source into. Plus `status: deprecated` for vanished sources, human-edit and `verified` protection via `.ragged/proposed/` (§6.4), and **stale-output cleanup**: parent ids derive from model-written titles, so a rebuild emits a differently-named tree as a matter of course — observed on two builds over the same three unchanged PDFs, different parent ids both times — and nothing else removes what the rename leaves behind.
2. **Segmentation** (§4.3). **Done.** Two passes: a planning call returns the parent's identity plus one verbatim `start_marker` per section, code cuts the text at those markers, then one synthesis call per section. Two passes rather than one call returning an array, because an array makes every concept share one `max_tokens` — which is what produced M1's flat ~2.5k-per-concept ceiling regardless of source length. Measured on 86k characters of PDF: retention 9% → 48%. A section that fails is recorded and skipped, so one 503 costs one section rather than the document.
3. **Link-weave** (§6.5). **Done**, in `synth/weave.py`. One call per concept written this run, given the full bundle index and the `candidate_links` synthesis proposed; returns `body_markdown` alone. §4.3's "every child links back to its parent in prose" is served by telling the pass its parent rather than by fabricating the sentence in the writer. Targets outside the index are unwrapped to plain text and reported — the spec's tolerance for broken links is for human authors, not for a generator holding the index. The ledger records the **post-weave** `body_sha256`, or the next run reads every concept as human-edited.

   Also done here, and named in the prompt-changes note below: the bundle's existing-concept index now reaches the synthesis cached prefix, snapshotted before discovery so the prefix stays byte-identical for the run.
4. **Per-directory `index.md`.** **Done**, as `okf.write_indexes` — one pass over the whole bundle read off disk, replacing the entries the pipeline used to accumulate. The three defects it owned are fixed with it:
   - **Labels came from the slug, not the `title`.** An index read `Delitos Informaticos Y Su Marco Legal En Argentina` while the frontmatter said `Delitos Informáticos y su Marco Legal en Argentina` — accents folded away by the slug, then English title-case applied to Spanish. Entries now carry the target's own `title` and `description`. The same fix applies to the parent concept's own `# Sections` list, which had the defect in a second place.
   - **The bundle title came from the project directory name**, so a bundle about Argentine cybercrime law was titled `# Ragged`, after the tool. `[bundle].title` now sets it, falling back to the directory name.
   - **The root index was a dead end for every child concept.** An entry whose concept owns a subdirectory now states how many concepts sit below it and links to that subdirectory's index.

Exit criterion: ingest a real corpus, validate clean, re-run and observe zero model calls. **Met** on the 3-PDF Spanish corpus: 18 concepts, okflint exit 0, and a second `build` reporting `skipped 3 unchanged source(s) - no model call` with `tokens in=0`. The corpus is smaller than the 20 files the criterion named; what the extra files would exercise is throughput, not a code path, and 21 units through `plan → synthesize → weave` covers the paths.

The real corpus also paid for itself twice over: all three link-repair modes in §6.5 and the `complete` flag in §5 came from running it, not from reasoning about it beforehand.

Both prompt changes that belong to M2 itself are done with item 3: the existing-concept index reaches the cached prefix (§6.4), and link-weave has its own prompt, where the wiki framing changes a decision — "a link asserts a relationship and the sentence around it says which" is the reason the pass is forbidden a "Related" section.

**Prompt calibration comes after M2, deliberately not before.** Few-shot examples are the strongest signal in a prompt — the model matches their length, tone and structure — but writing them now would bake in guesses: segmentation changes the unit shape, so a "one file, one concept" example would teach the wrong thing. The method after M2 is to take a generated concept a human promoted from `draft` to `stable` and use *that* as the example. It is then calibrated against the real corpus and the owner's judgement rather than against an invented document.

**M4 — agent contract.** Generated `AGENTS.md`, MCP server, `.mcp.json`. Three of the four MCP tools (`search`, `read_concept`, `lint`) are credential-free; only `ingest` inherits the write path's requirement, and the generated `AGENTS.md` must say so. Exit criterion: a session with no credential registers the server, answers a real question from the bundle, and reports lint findings.

**M3 — vectors + graph.** Embed, project, `graph.json`, FastAPI, sigma.js frontend with both edge layers and the τ slider. Where the ghost-edge idea gets its first real test. Note `search` works on index traversal before this lands; embeddings upgrade it rather than enable it.

**M5 — connectors.** `BigQuerySource` (asset family) and `BlobSource` (document family).

**M6 — extract `okf/` as a standalone library.** Gated, not scheduled.

`okf/` is the one part of this codebase with standalone value: it is bound to a published spec rather than to a provider, it has an objective conformance definition, it contains no model call, and nothing equivalent exists in Python today (`okflint` is a linter, not a read/write model). Anyone producing or consuming OKF needs it regardless of how they synthesize.

**Scope.** `concept.py`, `writer.py`, `validate.py`, `manifest.py` — the format layer only.

**Explicitly excluded, and this is the important half:**

- **`synth/` must not be published.** It is a thin protocol over two vendor SDKs that are already good. Shipping it means committing to chase every provider's API drift in exchange for indirection nobody asked for. This is the LangChain failure mode — a horizontal abstraction layer over plumbing — and the reason M6 is "a format library", not "a framework".
- `pipeline.py`, `cli.py`, `sources/`, `extract/` are application code. They stay here.

**Entry criteria — all of them, or this is premature abstraction:**

1. M2 shipped and a real corpus of ≥100 documents has been ingested and re-ingested.
2. The frontmatter policy (§4.2) has gone one full milestone without a breaking change. Today it has never met a real document, so it will move.
3. A second consumer exists that is not this pipeline — the MCP server (M4) counts. One caller is not evidence of a reusable API.
4. `parse`/`render` round-trip holds on a bundle authored by something other than ragged.

**Note on reach.** For *consumption* the MCP server beats a library: it works from any language and any agent with no import, whereas a Python library only serves Python producers. M6 is about the production side and about not making the next OKF tool reimplement frontmatter parsing — it is not the distribution strategy for the format.
