# okragged — Design

**One line:** drop files in, get out a portable OKF v0.2 knowledge bundle, a generated agent contract, and a served graph that shows both the links the knowledge *has* and the ones it *should* have.

**Status: M2, M3 and M4 built; M5 built but not yet exercised** — the pipeline and the bundle; `embed`, `project` and `graph.json` behind `okragged index`, with the read API and the sigma.js view behind `okragged serve`; and the agent contract (`agent/AGENTS.md` + the stdio MCP server) behind `okragged agent`. **All six M5 pieces are built**: the rename, the contract served over the protocol rather than named as a file, `add_source` in place of `ingest` on the agent surface, the Python API, and the write lock with its per-source commit. What remains is M5's exit criterion itself — a session driven from a checkout that is not the producer's — which depends on M6 publishing the package. See [§13 Milestones](#13-milestones) for what exists and what comes next. `README.md` covers installation and usage.

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
- Attested Computations. The OKF v0.2 family is supported on *read* (fields preserved), but okragged does not author them.

---

## 2. The three-layer model

Straight from the LLM Wiki pattern, with OKF as the interchange format for layer 2.

| Layer | Directory | Owner | Mutability |
|---|---|---|---|
| 1. Raw sources | `sources/` | The user | Immutable — okragged reads, never writes |
| 2. The bundle | `bundle/` | okragged (LLM-authored) | Rewritten on every ingest |
| 3. The schema | `agent/` | okragged (generated), user-editable | Regenerated on demand |

Plus a fourth thing that is *not* a layer because it is not knowledge:

| Sidecar | `.okragged/` | okragged | Derived, disposable, rebuildable from layers 1+2 |

**The sidecar rule is the load-bearing constraint of this design.** Nothing derived — embeddings, ANN index, 2D projection, content hashes, extraction cache — goes inside `bundle/`. `bundle/` must remain a directory you can `tar` up, hand to someone with no knowledge of okragged, and have them consume with `cat`. That is the entire value proposition of OKF; a `.faiss` file or an `embedding:` frontmatter key destroys it.

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
├─ .okragged/                  # sidecar — gitignored, rebuildable
│  ├─ manifest.json            # source hash → concept ids; the idempotency ledger
│  ├─ extracted/               # cached plain-text extractions, keyed by content hash
│  ├─ proposed/                # replacements for concepts a human owns (§6.4)
│  ├─ lock.json                # the one writer holding the project (§6.6)
│  ├─ staging/                 # the source in flight: its concepts + its manifest.json
│  ├─ pending-commit.json      # a commit to finish, if one was interrupted
│  ├─ embeddings.npy           # float32 [n_concepts, dim], L2-normalized
│  ├─ embeddings_meta.json     # provider, model, dim + row → concept id, embedded hash
│  ├─ layout.json              # 2D projection coordinates
│  └─ graph.json               # what the frontend fetches (nodes + both edge sets)
│
├─ agent/                      # layer 3 — the agent contract
│  └─ AGENTS.md                # generated: how to query this bundle
│                              # (the server is `okragged mcp`, registered by .mcp.json)
│
├─ src/okragged/
│  ├─ sources/                 # Source implementations (local, later gcs, bq)
│  ├─ extract/                 # bytes → text, per format
│  ├─ synth/                   # text → OKF concepts (Claude)
│  ├─ okf/                     # read, write, validate, index/log generation
│  ├─ vectors/                 # embed, knn, project
│  ├─ agent/                   # layer 3: search/lint, the contract, the MCP server
│  ├─ api/                     # FastAPI app
│  ├─ ledger.py                # the idempotency ledger (.okragged/manifest.json)
│  ├─ lock.py                  # one writer per project (.okragged/lock.json)
│  ├─ staging.py               # the per-source commit and its pending-commit record
│  └─ cli.py
│
├─ web/                        # Vite + React + TS + sigma.js
├─ okragged.toml               # config (see §11)
└─ pyproject.toml
```

`.gitignore` must contain `.okragged/`. The bundle is committed; the sidecar is not.

---

## 4. Data model

### 4.1 OKF v0.2 conformance

Three rules, all enforced by `okflint validate` in CI:

1. Every non-reserved `.md` file has parseable YAML frontmatter.
2. Every frontmatter block has a non-empty `type`.
3. `index.md` and `log.md` follow their defined structures.

`index.md` files carry no frontmatter except the bundle-root one, which carries exactly `okf_version: "0.2"`.

### 4.2 Frontmatter policy

okragged writes:

| Field | Policy |
|---|---|
| `type` | Required. From the type vocabulary — see below. |
| `title` | Always. Human-readable, derived from the concept, not the filename. |
| `description` | Always. One sentence. Reused verbatim in the parent `index.md` entry. |
| `resource` | When the concept maps to a followable artifact: `file:///…` for a local source, an absolute URL for web material. Omitted for abstract concepts. |
| `tags` | When the synthesis pass produces cross-cutting tags with ≥2 members. Never single-member tags. |
| `sources` | Always ≥1 entry: the file the concept derives from. `resource` required per entry; `id` present so the body can footnote it. `last_modified` from filesystem mtime. |
| `generated` | Always. `{ by: "okragged/<version>", at: <ISO 8601> }`. |
| `verified` | **Never written.** See §4.4. |
| `status` | `draft` on first synthesis; promoted to `stable` only by a human edit. Absent ⇒ `stable` per spec, so we write it explicitly. |
| `stale_after` | Only if the config sets a default TTL for a source; computed to an absolute date at write time. |
| `x_okragged_source_hash` | Extension field. SHA-256 of the source bytes this concept derives from. Producer-defined keys are explicitly allowed; consumers must preserve unknown keys. Re-ingest reads the ledger, not this — the field is here so a bundle handed to someone who has never heard of okragged still records which revision of the source it came from. |

**Type vocabulary.** `type` is a free-form string and the spec forbids imposing a taxonomy. But a *bundle* benefits from internal consistency, so okragged holds a per-bundle vocabulary in `okragged.toml` (`[types] allowed = [...]`). The synthesis pass is shown the current vocabulary and told to reuse an existing value where one fits, or propose a new one. New proposals are appended to the config and reported in the ingest summary — never silently. Defaults for a document corpus: `Document`, `Concept`, `Entity`, `Process`, `Decision`, `Reference`.

### 4.3 Concept granularity — one source file ≠ one concept

This is the most consequential modeling decision and it is deliberately *not* 1:1.

- A short memo → one concept.
- A 60-page report → a parent concept plus child concepts per substantive section, in a subdirectory named for the parent.
- A spreadsheet → one concept per sheet if sheets are semantically distinct, with a `# Schema` section per sheet.

The synthesis pass decides, constrained by: minimum ~150 words of substance per concept (below that, fold into the parent), maximum ~1200 words (above that, split). Every child concept carries the same `sources` entry as its parent and links back to it in prose.

### 4.4 The `verified` rule

okragged writes `generated.by: okragged/<version>` and nothing else in the trust family. It **never** writes `verified`, because `verified` records a confirmation event that actually happened, and writing `verified: {by: human:...}` because content looks correct forges the human-reviewed trust tier. A human promotes a concept by editing the file (or via a future `okragged verify` command that takes an explicit `--as human:<id>`); the pipeline never does it on their behalf.

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

`body_sha256` is the hash of the concept body *as okragged last wrote it*. A file on disk that hashes differently was edited by a human, and is protected (§6.4). This is **computed at read time, not stored as a flag**: a stored `human_edited: true` goes stale the moment the human reverts their edit, and a ledger that has to be told the truth twice will eventually be told it once.

Losing the ledger costs a full re-ingest, never correctness — so every read path degrades to "no record" rather than raising, and a skip additionally requires the recorded concept files to still be on disk.

### `lock.json`, `staging/`, `pending-commit.json`

Three entries, one job: make one writer at a time, and make each source's emit recoverable (§6.6). All three are sidecar state, so losing them costs a re-run and never correctness — a missing lock means "no holder", and a missing pending record means "no commit was in flight".

| | |
|---|---|
| `lock.json` | the writer holding the project: `operation`, `pid`, `host`, `started`. Created with `O_CREAT\|O_EXCL` — atomic on both platforms, which `fcntl` is not, and Windows is the primary one. Read paths never take it. |
| `staging/` | the **one unit** in flight — the concepts a single source produced, under `staging/bundle/`, plus `staging/manifest.json`, the ledger that commits with them. Emptied by each commit, so it never holds two sources' work |
| `pending-commit.json` | written immediately before that unit's files move and deleted after; while it exists, the commit is replayable. It names the unit, the concept ids to place and the stale ids to remove |

**A stale lock must not need a human.** The record names its holder so a dead one can be told from a live one: same host, the pid is probed (never with `os.kill(pid, 0)`, which on Windows *terminates* the process it asks about); another host, and age is the only test available, so a foreign lock goes stale after six hours. A crashed run leaving a lock file behind would otherwise replace the failure this fixes with a worse one.

### `embeddings.npy` + `embeddings_meta.json`

`float32[n, dim]`, L2-normalized at write time so cosine similarity is a dot product. Meta maps row → `{concept_id, embedded_body_sha256}`; a concept whose body hash differs from `embedded_body_sha256` is re-embedded.

Meta also records the `provider`, `model` and `dim` the matrix was produced with. Not a second opinion about configuration — it is what makes "swap the provider" (§6.8) safe to do by editing `okragged.toml` rather than only by deleting the file: vectors from a different model are not comparable, and at a different width they are not even stackable, so a mismatch discards the matrix and re-embeds instead of mixing two spaces in one file.

A concept that fails to embed keeps whatever vector it already had, recorded against the *old* body hash. The node still has a position, and the stale hash is what makes the next run retry it.

### `graph.json`

Precomputed server-side, fetched whole by the frontend. See §8.

---

## 6. The pipeline

```
discover → extract → segment → synthesize → link-weave → emit → validate → embed → project → serve
              │                     │            │                  │          │
           (cached)             (Claude)     (Claude)          (okflint)   (embed provider)
```

Stages 1–7 produce the bundle. Stages 8–9 produce the sidecar vectors. They are separable, and that separation is a command boundary: `okragged build` runs 1–7, `okragged index` runs 8–9, `okragged serve` will run the API + web. `build` never embeds — nothing in the build path uses a vector.

### 6.1 discover

Walk the `Source` (§9) and yield `(uri, bytes_or_stream, mtime)`. Compute `content_sha256`. Diff against `manifest.json`:

- Hash unchanged → **skip entirely**, including extraction and synthesis. No model call, no cost.
- Hash changed → mark for re-ingest.
- Hash unchanged but a section of it failed last run → re-ingest anyway. The ledger records `complete: false`, because a section lost to a 429 is source content missing from the bundle, and a source recorded as finished is never retried until its bytes happen to change. A failed *weave* does not count: the concept exists and is valid, only its links are missing (§6.5).
- In manifest but no longer present at source → mark its concepts for `status: deprecated` (per spec: keep deprecated concepts for links and history rather than deleting them). Deletion requires explicit `okragged prune`. Marked once: the source record is then dropped, so later runs do not re-announce it.
- Re-ingested, but the new run produced a differently-named tree → **delete** what the old one produced and this one did not. This is the opposite call from the bullet above, and the difference is who owns the name: a vanished source's concepts are knowledge whose input went away, while a renamed tree is the same knowledge under a dead id. Concept ids derive from model-written titles, so a rebuild renames routinely; without this sweep every run leaves a full copy of the previous one behind. A directory left holding only its own `index.md` goes too.

Ids the previous run wrote stay reserved for their own source across the whole run, released only when that source is itself re-ingested. Otherwise a rebuilt source can claim the id of a source that is about to be skipped.

### 6.2 extract

Bytes → plain text plus structural hints (headings, tables, sheet names). Keyed cache in `.okragged/extracted/<sha256>.json` so re-runs after a synthesis-only change cost nothing.

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

**Protected concepts.** If the on-disk body no longer hashes to `manifest.concepts[id].body_sha256`, or the file carries a `verified` block, synthesis does not overwrite it. Instead it writes the proposed replacement to `.okragged/proposed/<id>.md` and reports it. The human resolves. This is the one place where "the LLM owns layer 2" yields to "the human owns their verification".

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

Which is why each rewrite is its own commit (§6.6). The pass runs after every source has published, so it overwrites concepts a reader can already see; a body and a hash that disagree *is* the pre-weave-hash failure, reached by interruption rather than by design, so an interrupt here leaves every concept either woven and re-recorded or unwoven and recorded.

### 6.6 emit

Write concept files, then generate every `index.md` bottom-up, then append to `log.md` under today's ISO date, newest first. Log entries use the conventional bold leading word: `**Creation**`, `**Update**`, `**Deprecation**`.

**Indexes are read off the bundle, not accumulated during the run.** A directory whose concepts were all skipped has to be indexed exactly like one just written, and an entry must never disagree with the concept it points at — so every label is the target's own `title` and every line its own `description`, verbatim. Deriving a label from the slug instead folds accents away and then applies English title-case, which reads as damage in any other language.

An entry for a concept that owns a subdirectory states how many concepts are below it and links to that subdirectory's index. Without it the root index is a dead end: a reader concludes the bundle holds three documents when it holds eighteen. A directory no concept owns is listed in its own right. A `status: deprecated` concept is marked as such, since an index that lists it as an equal is the one place the deprecation would be invisible.

The root index's heading comes from `[bundle].title`, falling back to the project directory name — which names the tool rather than the knowledge unless the two happen to agree.

Writes are atomic per file (temp + rename). A *set* of files is a separate problem, and the honest claim about it is **recoverable, not atomic**: no filesystem call publishes several files at once on Windows, and pretending otherwise would be worse than not trying.

**The unit that commits is the source, not the run.** One source's emit lands whole or not at all: the concepts it wrote under their new ids, the removal of the stale output its rename left behind, and that source's ledger entry. Concept ids come from model-written titles (§5), so a rebuild renames that source's tree wholesale — and the window that has to be fenced is *inside* one source's emit, between writing the new names and deleting the old ones. A half-applied emit there is not a subset of the old tree, it is a mix of two namings, and stale-output cleanup reconciles against a complete emit or not at all.

**Across sources there is no such window.** Source A's new concepts beside source B's untouched old ones is a complete, valid, self-consistent bundle; stale-output cleanup is already scoped per source, and link-weave already tolerates a concept whose links are missing. So making the *run* the unit fenced nothing extra and cost everything an interrupted run had paid for: a forty-source build interrupted at source thirty-nine re-paid thirty-nine sources' worth of model calls. Resumability is worth more than whole-run atomicity, and this is where that is decided.

A run stages one unit at a time, and each commit is fenced by a record on disk:

1. save the ledger into `.okragged/staging/manifest.json`
2. write `.okragged/pending-commit.json` — from here the commit is replayable
3. move each staged concept into `bundle/`
4. delete the stale output the record names
5. `.okragged/staging/manifest.json` → `.okragged/manifest.json`
6. delete the staging area and the record

Every step is idempotent and guarded by what is on disk rather than by a step counter, so the next run replays from wherever a crash landed. Nothing moves or deletes the published tree as a whole, so unlike the whole-run swap this replaced there is no moment with no bundle at all. **The record is what says which of the two answers is right**, and it is written only once the unit is complete:

- **a record, with or without a staging area → finish it.** Its presence means the unit was worth publishing and only the placing is unfinished.
- **a staging area with no record → roll it back.** That one source was interrupted before it earned one. The sources committed before it stand, only that one is redone, and its staged files are left in the sidecar — unpublished, but not deleted at the moment of failure — until the next run reports them and clears them.

Either way the next run *says* what it found; a build that repaired this silently would be hiding the fact that an earlier one died.

**The ledger lands with the unit it describes or not at all**, which is why it stages too. A ledger that outran the bundle would have the next run's content-hash skip believe work was done that was not, and the corpus would quietly lose those sources — so the property is *per source* now rather than per run: a source recorded in the ledger is a source whose concepts are published. Layer 3 is written after the commits for the same reason — a contract beside concepts that never landed describes ones that do not exist (§10).

**Link-weave runs after every source has committed, and rewrites concepts that are already published.** That is deliberate and it is why the pass sees the full index of what the run wrote (§6.5). Two consequences, both already sanctioned rather than new:

- A source committed before the pass is published with a pre-weave body hash and no cross-links. That is exactly the state a *failed* weave produces, and §6.5 already says such a source is not marked incomplete, because the concept is valid and only its links are missing. An interrupt before the pass therefore needs no new state and is not retried for the links it lacks.
- Each rewrite is a commit of its own, so an interrupt mid-weave leaves every concept either woven and re-recorded or unwoven and recorded. Never woven against a stale hash — that mismatch is read as a human edit (§6.4), which would protect the concept from every later run.

**Indexes, `log.md` and `okf-base.yaml` are outside all of this, and do not need to be in it.** `write_indexes` reads the whole bundle off disk rather than accumulating during the run (above), so a run that dies leaves indexes that do not yet mention its newest concepts and *any* later run regenerates them, including one that changes nothing. `okf-base.yaml` is the same. `log.md` is a narration nothing reads back; `_finalize` still runs in a `finally`, so an interrupt still records the sources that did commit.

**What this costs, stated rather than discovered.** A source interrupted mid-emit loses the model calls it paid for and is redone; every source before it is not. The extraction cache survives, so even that re-payment is synthesis and not extraction. The remaining sharp edge is that a run interrupted before link-weave publishes concepts that stay unwoven until their source changes — the price of publishing early, and the same outcome the pass already has when the provider refuses.

**One writer at a time.** The CLI, the MCP server's `add_source` and the Python API are three writers to one directory, so a real run holds `.okragged/lock.json` (§5) for its duration. `okragged index` takes it too — it reads a bundle a build may be committing into. **Read paths never take it**: `search`, `read_concept`, `lint`, `check` and `serve` keep answering while a write is in flight, and so does `build --dry-run`, which writes no bundle and no ledger. The revision model that many *remote* writers need is M7's problem (§13).

### 6.7 validate

Two layers, because neither alone is sufficient:

1. **Built-in checks** — the three conformance rules plus W1–W5 warnings. Always run, no dependencies. This is the floor.
2. **okflint** (pinned, verified against 0.3.1) — the normative gate. `okflint validate --manifest okf-base.yaml <bundle>`; exit 0 required.

**okragged generates `okf-base.yaml`** at the project root, because okflint's `validate` always resolves a manifest (default `okf-base.yaml`) and exits 2 — "bad manifest" — without one. It cannot be run manifest-free. The generated manifest is minimal:

- `base.roots` — non-empty list of `{path, exclude_patterns}`, required.
- `base.reserved_files` — must contain both `index` and `log` keys, required.
- `hygiene.broken_links: warn` — the spec explicitly permits broken links, so they must never fail the gate.
- `hygiene.unknown_fields: off` — otherwise `x_okragged_source_hash` is flagged, and producer-defined keys are spec-legal.
- **No `profile` block.** A profile pins required fields and controlled vocabularies per type; that is the bundle owner's policy call, and enforcing one would reject the new types synthesis proposes (§4.2). Add it once a vocabulary settles.
- **No `okf_version` key.** okflint 0.3.1 recognises only `0.1` and prints a warning for anything else. The bundle-root `index.md` is where the bundle declares `0.2`.

When okflint is absent, or the manifest is missing, validation reports *why* it was skipped rather than silently degrading to the floor.

`okflint audit` takes `--bundle`, not a positional path, and is a separate diagnostic (split candidates, inventory) rather than part of the gate. `okflint index --apply` is **not** used — okragged generates its own `index.md` files in 6.6 with descriptions pulled from frontmatter; `okflint index` stays available as a repair tool for hand-edited bundles.

The okflint subprocess must decode as UTF-8 explicitly — `text=True` inherits the locale codepage and okflint emits characters outside cp1252. That is one instance of a project-wide rule: **never trust a platform default at an I/O boundary.** The full policy, and the AST test that enforces it, are in `README.md` → Encoding policy.

### 6.8 embed

**Nothing in the build path uses embeddings.** They exist for §8 — graph layout and the missing-link lint — so they are needed at M3, which sits behind M4. Concepts are produced, validated and consumed without them.

**Local vs hosted is still an open decision.** The original argument for local (fastembed + `bge-small-en-v1.5`, 384-dim, ONNX, offline) rested partly on the Anthropic API having no embedding model. That reason expired when Gemini became the default provider — it has one, reachable with the key the build already needs. What survives is weaker: rebuilds are free and offline, and embedding a *query* at read time needs no credential. Against local: ~150 MB of `onnxruntime` dependencies for something the existing key already covers.

Note the asymmetry that makes this low-stakes: concept vectors are computed at **build** time, when a credential is present either way, and then stored. Reading them needs no credential regardless of who produced them. Only query embedding would, and `search` falls back to index traversal.

Decide it by measurement: if local `bge-small` produces useful ghost edges on the real corpus, keep it. If the signal is noise, compare against `gemini-embedding-001`. Either way the vectors live in `.okragged/` — swapping providers is deleting `embeddings.npy` and re-embedding, with zero impact on `bundle/`. This is the sidecar rule paying for itself.

**What is built is therefore the pluggable interface, not the answer.** `vectors/providers/` mirrors `synth/providers/` exactly — one thin module per backend exposing `NAME`, `DEFAULT_MODEL`, `ENV_VARS`, `WINDOW_CHARS`, `credential_present()` and `embed()`, registered in `PROVIDERS` and selected by `[vectors].provider`. Both are implemented: `gemini` (`gemini-embedding-001`) because the build already requires that credential, and `fastembed` (`bge-small-en-v1.5`) behind an optional extra so the default install does not carry onnxruntime for a question nobody has answered yet. Chunking is per-provider (`WINDOW_CHARS`) rather than global, because the window is a property of the model.

Whichever wins: embed at **concept granularity** — one vector per concept file, over `title + description + body`. Concepts longer than the model's window are embedded as mean-pooled chunks; the chunk vectors are discarded, only the concept vector is kept.

Chunk-level vectors are deliberately not stored in phase 1. The retrieval path is progressive disclosure (§7), not chunk RAG; adding a chunk index before there is evidence retrieval quality demands it is building the thing this design argues against.

Only concepts whose `body_sha256` changed are re-embedded.

A failure to embed one concept is an error recorded against that concept, never an aborted run — the same rule as a failed synthesis unit (§6.4). The bundle is unaffected either way: `embed` reads it and writes nothing into it.

### 6.9 project

Embeddings → 2D. **UMAP** (`umap-learn`) with a fixed `random_state` so the layout is stable across runs — a graph that reshuffles on every ingest is unusable. Falls back to PCA when `n_concepts < 15` (UMAP is unstable at that size) or when `umap-learn` is unavailable (it pulls numba, which is a heavy dependency; it is an optional extra). PCA is plain NumPy — centre, SVD, first two components — rather than a scikit-learn import, so the fallback carries no dependency of its own. The coordinates are written to `layout.json` as well as into `graph.json`, so the projection stage has an output of its own.

The seed only fixes the *fit*. Refitting with new points can still move the whole layout, which is Risk 5 and is not solved here; the fix is `umap.transform()` against a persisted fitted model, which needs the model pickled into the sidecar and an explicit `reindex --full` to refit. Neither is built.

kNN is brute-force cosine over the normalized matrix — `n × dim` float32 is 1.5 MB at 1000 concepts and 384 dims. NumPy handles this in milliseconds and the code is twenty lines. Swap to `hnswlib` only if a real corpus crosses ~100k concepts; the full similarity matrix is `n²`, which is what will run out first.

---

## 7. Retrieval — out of scope

Retrieval strategy is deliberately **not** decided in this document. The objective is producing conformant OKF bundles; how an agent later queries one is a separate question, settled empirically against a real corpus rather than argued up front.

What this section fixes is only the constraint that keeps the question open: retrieval lives entirely in `.okragged/`, so it can change without touching `bundle/`. Embeddings in §8 exist to serve the graph (layout + lint), not to commit to a retrieval design.

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

**Ghost edge definition:** pair `(a, b)` such that `cos(a, b) ≥ τ` (default `τ = 0.82`, configurable), `b` is in `a`'s top-k neighbours (default `k = 8`), and there is **no** markdown link between them in either direction. Ghosts are computed only on the upper triangle to avoid duplicates — so the top-k condition is satisfied by *either* end, since a pair reachable from one direction is the same pair.

Three details the schema does not show:

- **`trust` is derived, never stored.** It is read off the concept's `verified` block per the spec's tier rules — a `human:` actor makes it `human-reviewed`, any other verifier `machine-confirmed`, none `unverified`. Writing it back into the concept would forge the tier (§4.4), so the graph computes it on every build.
- **Every concept is a node**, including one that failed to embed. It has no vector, so it has no position and sits at the origin, and it takes part in no ghost edge — but its authored links are still in the graph. Dropping the node instead would make the graph silently under-report the bundle.
- **`stats.broken_links`** counts link targets that resolve to no concept — the same condition as the `W2` warning, counted rather than listed.

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

**Built**, as `web/` behind `okragged serve`.

**sigma.js v3 + graphology**, WebGL. d3-force is nicer to style but degrades past ~1–2k nodes, and a document corpus of any size will exceed that. Positions come precomputed from UMAP, so no force simulation runs in the browser — sigma is used as a renderer, not a layout engine. Layout is therefore deterministic and instant.

Frontend fetches `graph.json` once and does all filtering client-side. Selecting a node fetches `GET /api/concept/{id}` for the rendered markdown in a side panel.

Two things the build settled that the encoding above states as intent:

- **A ghost edge is thin and pale, not dashed.** sigma v3 ships no dashed edge program, and writing one is more machinery than the distinction needs — weight and colour already separate the two layers at a glance. `web/src/encoding.ts` is the single place the encoding lives, so the legend and the renderer cannot drift.
- **The τ slider's floor is the configured τ, not 0.** `graph.json` holds only pairs that already passed τ *and* the top-k gate, so the slider explores `[τ_config, 1]`. Calibration is therefore a downward search from a deliberately permissive default — lowering τ below the configured value needs a re-`index`, not a drag.

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

**Built** (M4), as `src/okragged/agent/`. `pipeline.ingest` regenerates `agent/AGENTS.md` on every run, after the commit that publishes the bundle (§6.6), and `okragged agent` regenerates it offline from an existing bundle.

### The two layer-3 artifacts are not one artifact

`AGENTS.md` and `.mcp.json` were written by one function and regenerated together until M5's split. They should not be, because only one of them is derived from the bundle.

**`AGENTS.md` is a function of the bundle.** Concept count, structure tree, type vocabulary — change the bundle and the file must change with it. That is what the rule above is about: a contract written beside concepts that did not land describes concepts that do not exist, so it is rewritten after every commit and never before.

**`.mcp.json` is a function of nothing.** Since M5 the entry is `{"command": "okragged", "args": ["mcp"]}` — identical bytes whether the bundle holds no concepts or a thousand. Under M4 it named the generating interpreter and the generated `agent/mcp_server.py`, so it genuinely was derived from the producer's tree and had to be rewritten when that tree moved. The console script deleted that dependency (M5 item 1); regenerating it on every build outlived the reason for it.

**And it is the one file here okragged does not own.** `.mcp.json` is the MCP config for whatever agent works in that directory, shared with every other server registered in it. Merging into somebody else's config, unasked, on every build, for a value that never changes, is a write with nothing behind it. So registration is a deliberate act — `okragged agent --register`, or `okragged.register()` — and a build only *reports* that the server is unregistered. The write is temp-file-plus-rename for the same reason: it carries other people's data, and a truncated write there costs them every other server in the file.

Three consequences worth stating. `.mcp.json` is **not gitignored**, because whether a machine's MCP config belongs in a repository's history is its owner's call. The MCP server's `add_source` still refreshes `AGENTS.md` — it just changed the bundle, so that is honest — and never touches the config of the consumer who registered it. And `okragged.build()` no longer needs to differ from `okragged build`, because the surprise its old default guarded against was this file specifically.

### `agent/AGENTS.md` (generated)

The layer-3 schema doc, regenerated on each ingest. Contains: the bundle's directory structure, the current type vocabulary with counts, the four operations (search / read_concept / lint / add_source) and how to invoke each, the trust-tier convention, an explicit instruction that the query path is *read `index.md` first, then read whole concept files* — not "search for chunks" — and the registration entry a *foreign* project pastes into its own config, which is the one place a path to this root belongs and is therefore absolute.

It carries no timestamp, deliberately: the same bundle must regenerate to the same bytes, or a contract that is read as documentation churns on every build.

### The server — `okragged mcp`

A subcommand of the console script, not a generated launcher (M5, §13). It takes its bundle from `--project` or `OKRAGGED_PROJECT`, defaulting to the cwd; nothing is inferred from `__file__`, because a server that inferred its bundle that way could not survive being pip-installed. Registering it is therefore `{"command": "okragged", "args": ["mcp", …]}` — a name on `PATH` with no interpreter path and no script path, which makes any MCP-capable agent, Claude Code included, a consumer of the bundle with no okragged-specific code *from any tree at all*. The entry `okragged agent --register` writes into the producer's own root omits `--project`, since a client spawns the server with the config file's directory as its cwd; the copy in `AGENTS.md`, meant for someone else's config, names the root absolutely.

Four tools:

| Tool | Signature | Notes |
|---|---|---|
| `search` | `(query: str, k: int = 8) -> list[{concept_id, title, description, score}]` | Embedding kNN, or index traversal without a credential. Returns pointers, never bodies — the agent decides what to read. |
| `read_concept` | `(concept_id: str) -> str` | Raw file contents, frontmatter included. |
| `add_source` | `(text: str, title: str \| None) -> AddedSource` | Records knowledge no document states. Writes prose into `sources/` — **the path is generated, never accepted** — and stops there; the concept is authored by the next `okragged build`. Calls no model, so it needs no credential. |
| `lint` | `() -> LintReport` | okflint results + ghost edges + stale concepts + orphans. Ghosts are capped at the 20 strongest, with `ghosts_total` and a note; and a `graph.json` whose nodes no longer match the bundle is reported out of date rather than trusted, because a rebuild renames ids and the old graph is then about *different* concepts. |

And two resources, which is the other half of what makes that claim true. A consumer in another checkout cannot open `agent/AGENTS.md`, so the contract crosses the process boundary over the protocol instead of over the filesystem:

| Resource | URI | Rendered by |
|---|---|---|
| the contract | `okragged://contract` | `contract.py::render_agents_md` |
| the bundle's root index | `okragged://index` | `query.py::read_concept(config, "index")` |

Both call the function that writes the file, rather than a second renderer, so what is served cannot drift from what is on disk. Above them sits the handshake `instructions`: short, self-contained, and naming **no** path — it says what the bundle is and to fetch the contract. Three levels of disclosure, and the invariant they establish is checkable offline, which by §6.5's standard makes it a test rather than a convention: **no generated instruction text may name a path relative to the producer's root.** `tests/test_agent.py` asserts it over everything the server declares over the wire, with a meta-test pointing the check at the M4 text it replaced.

**No tool takes a credential, and that is a boundary rather than a convenience.** `search` has to work without one, and `add_source` is the tool that could most easily have needed one — it does not, because it records prose and leaves synthesis to `okragged build`. Nothing reachable over this protocol calls a model, so nothing reachable over it spends anybody's key; MCP over stdio has no identity to charge, and "whose key" would otherwise be settled by environment precedence. `add_source` is also deliberately additive: `ingest` came off this surface in M5 because a full sweep can deprecate a whole bundle (§13 M5), so nothing here can sweep the corpus either. Spending over the protocol is §13 M6.

**Why `add_source` and not `add_concept`.** Every concept traces to a source (§4.2): `sources:` carries at least one entry, each concept records `x_okragged_source_hash`, and the ledger maps source hash → concept ids. A concept written straight into the bundle would fabricate provenance or omit it, and would exist in the bundle while being absent from the ledger — so what the ledger describes and what the bundle contains would stop agreeing, which is the assumption stale-output cleanup, deprecation and human-edit detection all rest on. Writing the assertion as a source keeps every one of those and costs no new machinery. The run is partial, which suppresses the vanished-source sweep (§6.1) — safe by construction rather than by a check. One consequence to state rather than discover: asserting "x relates to y" mints a *third* concept linking to both; it does not rewrite x and y to link back, because link-weave only touches concepts written in the current run.

**The tool descriptions carry the load-bearing rules, not just `AGENTS.md`.** `instructions` is client-dependent and resources are an optional client capability, but a tool's description is shown wherever the tool is. So "returns pointers, never bodies", "read the index first", "read whole files, never a fragment" and "nothing here writes a `verified` block" live in the descriptions of the tools they constrain. A rule that exists only in the contract document is invisible to a client that ignores both other channels. Concept vectors are computed at build time and stored, but embedding a *query* is a read-time model call (§6.8). So `search` has two paths behind one result shape: kNN over `embeddings.npy` when the matrix exists and an embedding credential is present, and otherwise a traversal ranking over what the bundle already says about itself — titles and descriptions (which are what the indexes are made of, verbatim), the bodies at lower weight, and one hop along the authored links, so a concept the bundle links to a hit surfaces behind it. The result names which path answered, and the traversal path says why the other one did not. This is a ranking, not a retrieval framework: §7 keeps retrieval strategy out of scope, and the traversal ranking is deliberately simple enough to explain in a sentence and test without a model.

Two consequences of the layering worth keeping:

- **`search` returns pointers and `read_concept` returns whole files.** That is progressive disclosure (§7) expressed as a tool boundary — an agent cannot accidentally be handed chunks, because no tool returns one.
- **`add_source` goes through `pipeline.ingest`, not around it.** So it takes the project lock, and its emit is staged and committed like any other (§6.6): a client that disconnects mid-run leaves a bundle with that source whole or without it at all, never half of it. A `paths` subset additionally suppresses the vanished-source sweep (§6.1): a partial run never looked at the other sources, so their absence is not evidence that they are gone.

Nothing in this layer writes to the bundle, and nothing writes a `verified` block (§4.4) — the contract *explains* the trust tiers so that a consuming agent does not try to write one either. The MCP SDK is an optional extra: it pulls starlette, uvicorn and opentelemetry for a server that speaks over a pipe, and none of that is needed to produce or validate a bundle.

---

## 11. HTTP API and config

### API (FastAPI)

| Method | Path | Purpose | |
|---|---|---|---|
| `GET` | `/api/graph` | `graph.json` | **built** |
| `GET` | `/api/concept/{id}` | Frontmatter + rendered body | **built** |
| `GET` | `/api/search?q=&k=` | kNN, same as the MCP tool | **built** |
| `POST` | `/api/ingest` | Kick off a run; returns a job id | deferred |
| `GET` | `/api/ingest/{job}` | SSE progress stream | deferred |
| `GET` | `/api/lint` | Lint report | **built** |

The four read endpoints are wrappers over `agent/query.py`, `agent/lint.py` and the `graph.json` `okragged index` wrote, so `search` and `lint` cannot answer differently here than over MCP (§10). All four are offline; none writes anywhere.

**The two ingest endpoints are deferred, not cancelled.** They needed a job registry and progress events the pipeline did not emit; **the events exist now** — `ingest` takes a `progress` callback and reports each stage as it reaches it (§13 M5 item 3) — and the job registry does not, which is the half the graph view still does not need. Writing stays on the CLI and the MCP tool until something asks for it over HTTP. The rest of layer 3 already holds the invariant that matters: nothing in the API path writes to the bundle.

FastAPI is an optional extra (`pip install -e ".[api]"`) for the same reason the MCP SDK is: producing and validating a bundle needs neither.

### `okragged.toml`

```toml
[bundle]
root = "bundle"
okf_version = "0.2"
actor = "okragged/0.1.0"
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
provider = "gemini"         # gemini | fastembed (fastembed is an optional extra)
# model = "gemini-embedding-001"   # omit for the provider default
dim = 768                   # requested output width; fastembed's 384 is fixed
knn_k = 8
ghost_threshold = 0.82      # τ

[projection]
method = "umap"             # umap | pca; umap-learn is an optional extra
random_state = 42
```

`[vectors].provider` having a default is not Risk 4 being decided (§6.8). `gemini` is the one that costs no extra credential and no extra dependency, which makes it the right thing to start measuring *from*.

---

## 12. Risks and open questions

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Ghost-edge threshold is uncalibrated.** τ = 0.82 is a guess. A coherent corpus may make everything ≥0.9-similar, drowning the signal. | **Measured in M3, and the risk was real:** on the first corpus the median pairwise similarity is 0.896 and 150 of 153 pairs clear τ, so the selectivity comes entirely from `knn_k`. The mitigation stands — τ and k are config and only the last stage reruns — and the slider now exists to explore it. **Still open:** whether the fix is a higher absolute τ, a threshold relative to the corpus's distribution, or a smaller k. One saturated single-topic corpus cannot tell those apart, so the default has deliberately not moved. |
| 2 | **Extraction quality caps everything downstream.** Garbage text → garbage concepts, and no amount of prompt work fixes it. | Cache extractions and expose them; an `okragged extract --inspect <file>` command so extraction can be debugged independently of synthesis. |
| 3 | **Synthesis cost on large corpora.** 400 documents × ~3 units × Opus 5 at high effort is real money. | Cached system prefix (~0.1× on the bulk of input tokens), Batches API (50%), hash-based skip on unchanged files. Report estimated cost before a run and require confirmation above a configurable ceiling. |
| 4 | **Embedding provider is undecided** (§6.8). Local `bge-small` is weaker on domain jargon; hosted costs money and, for query embedding, a credential at read time. | **Still open, deliberately.** What is built is the pluggable interface with both backends registered, selected by config; the answer comes from measuring ghost-edge quality on the real corpus. Sidecar-only, so swapping is a re-embed that touches nothing in `bundle/`, and `embeddings_meta.json` records the provider so a swap re-embeds rather than mixing two vector spaces. |
| 5 | **Layout instability across ingests.** UMAP with new points added can reshuffle globally even with a fixed seed. | Fixed `random_state` — **done**, and it is only half the mitigation: it makes a run reproducible, not a refit stable. `umap.transform()` on a persisted fitted model would place new points into the existing layout, but it needs the fitted model pickled into the sidecar and an explicit `okragged reindex --full` to refit. Not built. |
| 6 | **Human edits vs. LLM ownership.** The wiki pattern says the LLM owns layer 2, but a human who verified a concept has a legitimate claim on it. | Body-hash comparison + `verified` detection + `.okragged/proposed/` (§6.4). Never silently overwrite. Built in M2. |
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
| **M3** | Vectors + graph | **done** |
| **M4** | Agent contract | **done** |
| **M5** | Distribution — package, MCP export, Python API | **in progress** — 6 of 6 built, exit criterion open |
| **M6** | Security layer — identity, and who may spend | **next** |
| **M7** | Publish — PyPI, and shipping the view | next |
| **M8** | Hosted server — one bundle, many consumers | proposed |
| **M9** | Connectors | phase 2 |
| **M10** | Extract `okf/` as a standalone library | gated — see below |
| **M11** | Grounded answers | proposed |

**M3 and M4 were deliberately swapped, and then the execution went the other way.** The reason for the swap still stands: the objective is that any model *consumes* the bundle, and that is served by the agent contract, not by the graph. The graph is diagnostic; MCP is the product.

M3's offline core was nevertheless built first. It cost nothing to take out of order, because `okragged index` is a separate command writing only into `.okragged/` — no build path, no `bundle/`, no dependency from `okf/` — so M4 begins from the same place it would have. What taking it early did not buy was the thing M3 exists for: τ stayed a guess until something ran the rule against a real corpus. **M4 came next**, and the rest of M3 after it.

---

**M1 — walking skeleton. Done.** `LocalFileSource` + markitdown + single-unit synthesis + emit + okflint + a generated `okf-base.yaml`. Pluggable providers (Anthropic, Gemini). One file in, one conformant concept out. 53 tests. No links, no segmentation, no vectors.

**M2 — real bundle. All four items done.** In dependency order:

1. **`manifest.json` + idempotent re-ingest.** **Done**, in `ledger.py` (`okf/manifest.py` was taken by the okflint manifest). Content-hash skip so an unchanged source costs nothing — not extraction, not the N+1 model calls segmentation turned one source into. Plus `status: deprecated` for vanished sources, human-edit and `verified` protection via `.okragged/proposed/` (§6.4), and **stale-output cleanup**: parent ids derive from model-written titles, so a rebuild emits a differently-named tree as a matter of course — observed on two builds over the same three unchanged PDFs, different parent ids both times — and nothing else removes what the rename leaves behind.
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

**M3 — vectors + graph.** Embed, project, `graph.json`, FastAPI, sigma.js frontend with both edge layers and the τ slider. Where the ghost-edge idea gets its first real test. Note `search` works on index traversal before this lands; embeddings upgrade it rather than enable it.

**The offline core is built**, as `okragged index` (§6.8, §6.9, §8):

- **`embed`** — `vectors/providers/` mirroring `synth/providers/`, with `gemini` and `fastembed` registered and neither one anointed (Risk 4 stays open). One vector per concept over `title + description + body`, mean-pooled over chunks past the provider's window, only the concept vector stored. A body whose hash matches `embedded_body_sha256` is not re-embedded, and one concept failing costs that concept alone.
- **`project`** — UMAP with a fixed `random_state`, PCA below 15 concepts or without `umap-learn`. Both extras (`umap-learn`, `fastembed`) are optional: numba and onnxruntime are not something a default install should carry to produce a bundle that never reads a vector.
- **`graph.json`** — nodes with derived `trust`, both edge sets, and the ghost rule of §8 over brute-force cosine kNN in NumPy.

Everything it writes is in `.okragged/`: `embeddings.npy`, `embeddings_meta.json`, `layout.json`, `graph.json`. `build` does not call it and the bundle does not know it ran.

**The rest is built too:** the four read endpoints of §11 behind `okragged serve`, and the sigma.js view in `web/` with both edge layers, the τ slider and the concept panel. Both extras are optional (`.[api]`, and `web/` builds only if you run `npm`), for the same reason `umap` and `mcp` are: a bundle is produced, validated and consumed without either.

**Exit criterion: ingest a real corpus, index it, and see the ghost edges rendered — τ answered by a corpus rather than by argument. Met**, and the answer was that the default is wrong. On the 18-concept Spanish corpus, embedded with `gemini-embedding-001` at dim 768 and projected with UMAP:

| | |
|---|---|
| concepts / authored links / ghosts / broken | 18 / 39 / 76 / 0 |
| median cosine similarity, all 153 pairs | **0.896** |
| pairs at or above τ = 0.82 | **150 of 153** |
| pairs at or above 0.92 | 22 |
| pairs at or above 0.95 | 4 |

**τ = 0.82 sits below the median, so it excludes almost nothing.** The 76 ghosts are 76 rather than 150 because `knn_k = 8` is doing the filtering — the top-k gate, not the threshold. That is Risk 1 landing exactly where §8 predicted ("everything in a coherent bundle is somewhat similar to everything else"), on a corpus of three PDFs about one statute.

What it does **not** settle: whether the fix is a higher absolute τ, a threshold relative to the corpus's own similarity distribution, or a smaller `knn_k` — one saturated single-topic corpus cannot distinguish those. Raising the default on this evidence alone would be fitting a constant to one sample. The slider is the instrument; the reading is recorded here, and the default moves when a second, less homogeneous corpus agrees.

**M4 — agent contract. Done**, in `src/okragged/agent/` (§10). Generated `AGENTS.md`, the stdio MCP server, and a `.mcp.json` entry that registers it — merged into whatever else is already registered rather than written over it. Three of the four tools (`search`, `read_concept`, `lint`) are credential-free; only `ingest` inherits the write path's requirement, and the generated `AGENTS.md` says so.

The credential-free requirement is what shaped `search`: it falls back from embedding kNN to a traversal ranking over the indexes, the bodies and the link graph, and reports which path answered. That fallback is not a stub — it is the path the exit criterion exercises, and on the real corpus it is the only one available, because `okragged index` needs the same credential.

The contract is regenerated at the end of every ingest — after the commit that publishes the bundle (§6.6) — so it can never describe a bundle that no longer exists, and by `okragged agent` for a bundle that was built elsewhere. It is generated *beside* `bundle/`, never inside it — layer 3 is a sibling of layer 2, and the sidecar rule (§2) is why.

Exit criterion: a session with no credential registers the server, answers a real question from the bundle, and reports lint findings. **Met** on the same 3-PDF Spanish corpus, driving the generated `agent/mcp_server.py` over stdio exactly as the `.mcp.json` entry spawns it: `search` answered a question about Ley 26.388 by traversal (reporting `no stored embeddings for this provider; run okragged index`) with the right concept first, `read_concept` returned it whole, and `lint` reported 18 concepts, okflint exit 0, no orphans, and no ghost edges *because the index has not run* rather than because there are none.

**M5 — distribution. All six pieces built, and deliberately ahead of connectors.**

Everything built so far assumes the reader is standing inside the producer's working tree. `.mcp.json` names a venv-relative interpreter and a relative script path; the server tells its client that `agent/AGENTS.md` is the contract; the only way to drive the pipeline is a CLI rooted at a cwd. §10 claims that registering the server "makes any MCP-capable agent a consumer of the bundle with no okragged-specific code", and **for a consumer outside that tree, it does not.** M5 makes that claim true.

**Why before connectors, which is a reordering.** M7 adds `Source` implementations, and a public API freezes the `Source` protocol (§9) — including the question of whether exhausting `discover()` constitutes a *complete* observation, which is what licenses the ledger to mark an unseen source `deprecated`. That semantics is cheap to settle now and expensive to retrofit, because retrofitting means auditing every `deprecated` mark ever written. Designing the contract once, with connectors in view but unwritten, is the whole reason for the swap.

**The name changes, and that is part of the milestone.** `ragged` on PyPI is an active Scikit-HEP library for jagged arrays, and it owns the top-level `ragged` module — so the collision is on import, not merely on the distribution name. This becomes:

| | |
|---|---|
| distribution | `okragged` |
| module | `okragged` |
| console script | `okragged` — one entry point |
| the server | `okragged mcp`, a subcommand rather than a second script |
| `.mcp.json` key | `okragged` |
| MCP resource URIs | `okragged://contract`, `okragged://index` |

**No hyphen, and one script.** A hyphenated distribution reads fine in `pip install` and badly everywhere else, and a second `okragged-mcp` binary buys nothing a subcommand does not — the registration entry becomes `{"command": "okragged", "args": ["mcp", "--project", "…"]}`, which is one name to learn and one thing on `PATH`. `okragged` is unclaimed on PyPI, so the distribution and the module can be the same word, which is the arrangement that never surprises anyone.

The URI scheme is the one irreversible choice here: `okf://` would claim the format's name for one tool's server, which is only defensible if the format library (M10) ever ships and owns it. `okragged://` is honest about who serves it.

**Six pieces.**

1. **Package. Built.** Console-script entry points rather than generated paths. This does not fix the `.mcp.json` defect so much as delete it: a pip-installed `okragged` on `PATH`, invoked as `okragged mcp`, needs no interpreter path, no script path, and nothing relative to a cwd. The server then needs an explicit target — `--project` or `OKRAGGED_PROJECT` — because a launcher that infers its bundle from `__file__` cannot survive being installed.

2. **Export the contract over the protocol, not the filesystem. Built** (§10). Three levels of disclosure: `instructions` short and self-contained at the handshake, the rendered `AGENTS.md` as `okragged://contract`, `bundle/index.md` as `okragged://index`. Both handlers call functions that already exist (`contract.py::render_agents_md`, `query.py::read_concept`), so there is no second renderer to drift from the file on disk. **The invariant this establishes:** *layer 3 crosses the process boundary over the protocol, never over the filesystem — no generated instruction text may name a path relative to the producer's root.* Checkable offline, so it is a test rather than a convention, and it has a meta-test pointing it at the M4 handshake and the M4 registration entry — both of which it catches.

   Built with it, because it is the same defect: `okragged mcp` with `--project` / `OKRAGGED_PROJECT`, the portable `{"command": "okragged", "args": ["mcp", …]}` entry, and the deletion of the generated `agent/mcp_server.py` — which existed only to be the thing `.mcp.json` pointed at, and after the entry names a console script nothing points at it.

   Credentials are a smaller problem than they look. MCP config carries `env` per server, so a consumer supplies its own key; and **no tool needs one at all**. Auth covers `add_source` and `search`'s embedding upgrade, not the server.

3. **A Python API, thin. Built**, in `src/okragged/__init__.py`. `Config` was already a pydantic model with derived roots, and `load_dotenv` already lived in `cli.py` and `server.py` rather than in the pipeline, so the core was credential-agnostic and constructible in memory before this landed.

   **The capability was never missing; the surface was.** `from okragged import config, pipeline; pipeline.ingest(config.load(path))` has worked all along — `__init__.py` was one line. So what this adds is a curated entry point: `configure`, `build`, `index`, `check`, `search`, `read_concept`, `lint`, `add_source`, `write_contract`, `register`, each taking `str | Path | Config` and delegating to the function the CLI calls. No new types, no `provider=` argument (see *what this must not become*), and no `Bundle` — a bundle is a directory (§2), so what comes back is what the underlying function already returned. The one thing the facade must earn is being thinner than reaching into the submodules, which is why it is ten functions and two re-exported names.

   **The two real changes are behavioural, and both were defects for a caller who is not a terminal:**

   - **`_finalize` wrote layer 3 unasked.** `ingest` now takes `contract`, defaulting to on for every caller: §10 depends on `agent/AGENTS.md` never describing a bundle that no longer exists, and a run that committed is the moment the bundle changed. This originally defaulted *off* for `okragged.build()`, because a function call that drops `.mcp.json` into somebody's repository is a surprise — and that asymmetry is now gone, because [the split below](#the-two-layer-3-artifacts-are-not-one-artifact) took `.mcp.json` off the build path entirely. What remains is one derived file written beside the bundle the caller just asked for, which needs no exception.
   - **`ingest` reported only on completion.** It now takes an optional `progress` callable receiving a frozen `Progress(stage, unit, index, total, detail)` — one event per stage per unit, over the eight stages in `pipeline.STAGES`. This is the callback whose absence deferred `POST /api/ingest` and its SSE stream (§11); the endpoint still needs a job registry, and that is M8's problem or a later one's. A reporter that raises is swallowed: a caller's broken logging must not abort a run that has already paid for model calls.

   **`import okragged` is silent and cheap, and that is a requirement rather than a nicety.** `markitdown` prints an ffmpeg warning when imported and `extract/` imports it, so a package whose import writes to somebody else's stderr is unusable in a pipe — and for `okragged mcp`, stdout *is* the protocol. Every import in the facade is function-local, with a module `__getattr__` (PEP 562) for the two names, and a test asserts both that the import prints nothing and that it pulls in neither the extraction path nor a provider.

4. **`ingest` comes off the tool surface entirely. Built.** It runs `_deprecate_vanished` on any full sweep, so a recipient holding the bundle *and* the sidecar — ledger present, `sources/` absent — gets an empty `discovered`, every recorded source reads as vanished, and **every concept is marked `deprecated`**. The bundle alone is safe (no ledger, nothing to deprecate); it is specifically the combination, which is exactly what you would ship to make search work.

   Gating the tool would close it. Removing it closes it *structurally* — the branch becomes unreachable over the protocol rather than guarded on it, which is the same preference §6.5 states about repairs over prompt rules.

   **Partial ingest goes too, and that is not an oversight.** `ingest(paths)` is safe, since a partial run suppresses the sweep. But the consumer this milestone exists for works in a different tree and cannot see the producer's `sources/`, so it cannot name a path; the only caller who could is the owner, who has a terminal. It is a tool nobody able to reach it can use. Re-ingest is `okragged build`, and it stays where the credential and the sources already are.

   The surface therefore stays four tools and changes which four: `search`, `read_concept`, `lint`, `add_source`. All credential-free, one that writes — the same shape as M4, with the destructive member replaced by an additive one. §10 is rewritten when this is built, not before.

5. **`add_source` — the write tool that replaces it, and the reason it is not `add_concept`. Built**, in `agent/write.py`. Knowledge worth recording is not always in a document: two tables are related in a way no paper states, and someone knows it. That has to be able to enter the bundle.

   The tempting shape is a tool that writes a concept directly, and it breaks the invariant everything else rests on — **every concept traces to a source.** §4.2 requires `sources:` with at least one entry, every concept carries `x_okragged_source_hash`, and the ledger maps source hash → concept ids. A directly-written concept either fabricates provenance or violates the policy, and it appears in the bundle while being absent from the ledger — so "what the ledger describes" and "what the bundle contains" stop agreeing, which is the assumption stale-output cleanup, deprecation and human-edit detection are all built on.

   Writing the assertion into `sources/` instead costs nothing architecturally and keeps all of it: provenance points at a file that exists, the ledger stays complete, and segmentation, link-weave and validation apply, so the new knowledge is woven into the graph rather than sitting beside it. The tool writes `sources/conversations/<date>-<slug>.md` and ingests **that path only** — a partial run suppresses the vanished sweep (§6.1), so it is safe by construction.

   **The tool generates its path, it never accepts one.** The slug comes from the assertion; a caller-supplied path is a traversal write, and this is the mirror of the confinement `read_concept` already does on the read side.

   **One honest limitation to state rather than discover.** Asserting "x relates to y" mints a *third* concept that links to both; it does not rewrite x and y to link back. Link-weave only touches concepts written in the current run, which is the forward-only property M2 settled. The relationship is in the graph and it is traceable — it just lives in its own node, and the reverse direction is what ghost edges are for.

6. **A lock and a staged emit. Built**, in `lock.py` and `staging.py` (§5, §6.6). Not deferred to hosting: after M5 the CLI, the MCP server and the Python API are three writers to one directory, and nothing detected them interleaving. `.okragged/lock.json` makes it one writer per project — exclusive creation rather than `fcntl`, because Windows is the primary platform — and the emit stages into `.okragged/staging/` and commits behind a pending-commit record.

   **The claim is recoverable, not atomic, and the difference is the whole design.** No filesystem call publishes several files at once on Windows, and asserting one would be worse than not trying. What the record buys instead is that an interrupted commit is *detectable and completable*: a record means finish it, a staging area with no record means roll it back, and the next run says which it found.

   **The unit is the source, not the run**, which is a correction of what this first shipped as. Concept ids derive from model-written titles, so the hazard is not a partially updated tree but a mix of two namings — and that hazard lives *inside* one source's emit, between writing its new concepts and removing the stale output its rename left behind. Across sources there is nothing to fence: source A's new concepts beside source B's untouched old ones is a complete, valid bundle, and stale-output cleanup is already scoped per source. Fencing the whole run therefore protected nothing extra and cost every source an interrupted run had paid for — a forty-source build interrupted at thirty-nine re-paid thirty-nine. Resumability is worth more than whole-run atomicity; §6.6 states the trade and what an interrupt at each phase leaves.

   **The ledger lands with the source it describes or not at all.** That is the half that must not be relaxed: a ledger that outran the bundle would have the content-hash skip believe work was done that was not, and the corpus would lose sources silently. The other direction only costs a re-synthesis, which is why the boundary could move at all.

   **Three things this deliberately does not do.** No revision model: `expectedRevision` and conflict resolution are for many remote writers, and that is M7 criterion 3. No lock on any read path — `search`, `read_concept`, `lint`, `check` and `serve` answer while a write is in flight, because a consumer holding a directory of markdown files needs permission from nobody, and a reader a build could refuse would undo the point of the format. And no attempt to make the indexes, the log or `okf-base.yaml` transactional: they are derived from the bundle on disk, so a later run regenerates them (§6.6).

**What this must not become.** The API is an entry point to the pipeline, not an abstraction over it. `synth/` stays unpublished for the reason M8 gives below, and it applies with more force to a public API: a `provider=` argument in a documented signature is a promise to track every vendor's drift forever. Nor does the first version return a `Bundle` object — the load-bearing property of this design is that a bundle *is* a directory (§2), and an object hands back a different answer to a question nobody has asked yet. Summaries and paths.

**Exit criterion**, in three parts, because the milestone has three claims:

1. **Portable.** From a checkout that is not the producer's, with no embedding credential: `pip install okragged`, register `okragged mcp` against a bundle elsewhere on disk, read the contract over the wire, and answer a real question from it — with no path to the producer's tree appearing anywhere in the consumer's configuration or in anything the server says. Until that session has happened, this is reasoning about a consumer nobody has been.
2. **Safe.** That same consumer, handed the sidecar as well, has no tool that can sweep the corpus: the surface offers no `ingest`, and the bundle cannot be deprecated wholesale by anything reachable over the protocol.
3. **Additive.** `add_source` asserts a relationship that no source document states, and after the partial run that follows, the assertion is a concept with real provenance, linked into the graph, and a second `okragged build` over the corpus leaves it alone.

**M6 — security layer. Next, and it is what M5's own audit turned up.**

M5 made the bundle reachable from another tree. It did not give the protocol an answer to *who is asking*, and until it does, nothing reachable over the protocol may spend money.

**The problem, stated exactly.** MCP over stdio has no identity. The server is a subprocess of whatever client spawned it, running as whoever ran the client; there is no channel on which a caller could present a credential and no way to attribute a call to a person. So a tool that called a model would spend a key chosen by environment precedence — `load_dotenv` does not override an existing variable, so the consumer's environment wins and the producer's `.env` is the fallback. Three outcomes, selected by accident: the consumer's key pays, *the producer's key pays for a stranger's writes*, or the tool errors. §10's own reasoning does not cover this. It argues that authenticated **reads** are theatre under M5 because the consumer holds the bytes and can `cat` them, which is true; filesystem permissions decided nothing about an API quota.

**What M5 did about it, and why that is not the milestone.** `add_source` was cut back to recording: it writes prose into `sources/` and the concept is authored by the next `okragged build`, on the operator's machine with the operator's key. That removes the question rather than answering it, and it is the right trade for a milestone whose model is single-tenant — but it also means the one genuinely useful write is deferred to a human running a command. Answering the question is this milestone.

1. **An identity to attribute a call to.** Not necessarily a person: a token bound to a project, or a client-supplied key the server refuses to fall back from, is enough to make "whose spend is this" answerable rather than accidental. What is not enough is inferring it from the environment.
2. **`.env` stops being ambient to the server.** `okragged mcp` should not inherit the producer's secrets merely because it was pointed at their directory. `okragged build` keeps reading `.env` — a human typed it there — but a subprocess somebody else's client spawned is a different trust position, and today they are the same code path.
3. **Spending is a scope, not a capability.** A tool that costs money needs an authorization scope the ordinary consumer's token does not carry, plus a rate limit: bounded per call is not bounded per hour. This is the item M8 lists as hosting's problem; it is not, it is this milestone's, and hosting inherits it.
4. **Writes become opt-in per server instance.** `okragged mcp` should be able to serve a bundle read-only. M5 introduced a producer/consumer split and gave it no way to say "readable by others, writable by me".

**Exit criterion.** A synthesizing `add_source` back on the tool surface, refused for a consumer with no scope, charged to a named identity for one with it, with a rate limit that survives a loop — and `okragged mcp` able to serve the same bundle with writes off entirely.

**M7 — publish. M5's exit criterion depends on it.**

M5 made the tool installable. Nothing has been installed *from anywhere*: `okragged` exists as an editable checkout, and M5's own exit criterion opens with `pip install okragged`, which nothing can satisfy until something is published. So this is not packaging polish — it is the sentence that closes the previous milestone.

**A defect first, and it is separable from the release. Fixed.** `api/app.py` resolved the built view as `config.project_root / "web/dist"` — inside the **user's corpus directory**, beside their `sources/` and `bundle/`. That is only correct when the project root happens to be this checkout. An installed user who installed Node and built the frontend would still have had to drop `web/dist/` next to their own corpus for `okragged serve` to find it, and would otherwise get the four endpoints and a 404 at `/`. This is the defect class §10 and M5 exist to eliminate — a path that means something only inside the producer's tree — and it survived because the view was written before the milestone that made that the governing rule. It is now `api.app.STATIC_DIR`, resolved from the package as `Path(__file__).parent / "static"`, with Vite's `outDir` pointing there and the wheel shipping it as an artifact: one build output, at one location, true for a checkout and an install alike. No fallback to the project root remains, because a fallback to a path that means nothing outside this tree is the defect wearing a second hat.

**Then how the view reaches a wheel**, which is the open question. `[tool.hatch.build.targets.wheel]` packages `src/okragged` only, so `web/` reaches nothing today:

| | cost |
|---|---|
| commit the built bundle as package data | minified JS in git history — ugly, bounded, and visible |
| a build hook that runs `npm` | clean history, but installing from an sdist then needs Node |
| assets in the wheel, absent from the sdist | correct, and the most moving parts |

The first is preferred: its ugliness is one file, regenerated deliberately, versus a build hook that is invisible until somebody's `pip install` fails on a machine without Node. The view is optional and the endpoints are not — `serve` must keep starting without it.

**And the metadata a publish needs**, none of which exists: `license`, `readme`, `classifiers`, `[project.urls]`, an author. What is there today builds a wheel that works locally; it is not metadata anyone should find on an index.

**What publishing commits to, and the reason to think before doing it.** The `okragged://` resource scheme, the four tool names and their result shapes, and the public API's nine functions all become things other people can depend on. `okragged` is unclaimed on PyPI, so claiming the name early has value — but a `0.1.0` that anyone installs is a promise, and §13's own standard is that a public name is expensive to reverse.

**Exit criterion.** M5's first clause, run for real: on a machine that is not the producer's, `pip install okragged` from the index, register `okragged mcp` against a bundle, read the contract over the wire, answer a question from it — and `okragged serve` renders the graph without Node installed.

**M8 — hosted server. Proposed, and a different product from M5.**

M5 makes the bundle *portable*: you hand someone a directory, they install a package, they run their own server over their own copy. M7 makes it *shared*: one machine holds the bundle and many consumers connect to it. That inverts several properties, which is why it is a separate milestone rather than the end of M5.

**What it buys, and it is not only convenience.** A remote consumer needs nothing at all — no bundle, no sidecar, no credential — because `read_concept` already serves content over the protocol. And because the host holds the embedding key, remote clients get the **embedding** search path, which under M5 a credential-less consumer can never have. Hosting is the only arrangement in which semantic search reaches someone who has no key.

**What it inverts, and each is an entry criterion rather than a task:**

1. **Credential-free stops being a feature and becomes an exposure, and the exposure is *reading*.** "Three of four tools need no credential" reads, on a port, as *anyone who can reach it reads the whole corpus* — `read_concept` returns whole files and `search` enumerates them. The write path is the smaller half of this. The spec's answer is OAuth 2.1 with the server as a resource server, and §12 Risk 7 is the reason it cannot be deferred to "later".

   **Authenticated reads are a mode, not a default, and only here.** Under M5 the consumer holds the directory and can `cat` it, so a credential demanded over the protocol while the same bytes sit readable on their disk is theatre — filesystem permissions already decided. Read auth means something exactly when the server is the only path to the bytes.

2. **The bundle is reachable through tools and never as files, and that is what makes authentication sufficient.** A hosted server must not expose `bundle/` — no static mount, no download route, no path that hands over a file. Every byte that crosses the boundary crosses it as a tool result, which is the same surface `search` and `read_concept` already define and already confine. Get that right and authenticating the tools protects everything; get it wrong and no amount of auth on the tools matters, because the files are beside them.

   This is checkable offline, so it is a test rather than a convention. Note `serve` today mounts the built view at `/` (§11) — the built view, which is public by construction. That mount must never grow to include the bundle.

   **Per-concept scoping is deliberately not here.** Deciding *what* a given consumer may see is a separate question from *whether* they may connect, and nothing needs it yet — a bundle a host is willing to serve is one it is willing to serve. It is staged in `NEXT-STEPS.md`, not committed, and it would have to stay coarse if it ever lands: indexes carry every child's `title` and `description` verbatim, so serving the map while withholding the bodies still discloses the map.

   **Access control never enters the bundle, in any milestone.** No `access:` or `visibility:` frontmatter key. OKF has no notion of permissions, and a bundle whose meaning depends on a policy engine is no longer something you can `tar` up and read with `cat` — the sidecar rule (§2) applied to authority instead of to derived data. Access lives in the server, which is disposable.
3. **Concurrency stops being an assumption and becomes a guarantee.** M5 ships a lock and a staged emit, which is enough for one machine's three writers. Many clients need the revision model: a mutation carries an expected revision and a stale one fails *before* anything becomes visible. Ids derive from model-written titles, so a half-applied rewrite is not a subset of the old tree — it is a mix of two namings.
4. **Writing becomes remote spend on the host's key** — but the scope model and the rate limit that answers it are **M6's**, not this milestone's, because the same question already exists over stdio. What hosting adds is that the host's key is the *only* key: under M6 a consumer can be made to bring their own, and here there is nowhere else for the cost to land.
5. **`verified` loses its referent.** §4.4's trust tiers assume one owner who confirms. With several humans able to promote a concept, "a human reviewed it" has to say *which* human, and the spec's actor field is where that lands.

Transport is the least of it: the SDK speaks streamable HTTP, and M3 already brings uvicorn.

**M9 — connectors.** `BigQuerySource` (asset family) and `BlobSource` (document family). Note the `Source` protocol they implement is settled by M5, not by them.

**M10 — extract `okf/` as a standalone library.** Gated, not scheduled.

`okf/` is the one part of this codebase with standalone value: it is bound to a published spec rather than to a provider, it has an objective conformance definition, it contains no model call, and nothing equivalent exists in Python today (`okflint` is a linter, not a read/write model). Anyone producing or consuming OKF needs it regardless of how they synthesize.

**Scope.** `concept.py`, `writer.py`, `validate.py`, `manifest.py` — the format layer only.

**Explicitly excluded, and this is the important half:**

- **`synth/` must not be published.** It is a thin protocol over two vendor SDKs that are already good. Shipping it means committing to chase every provider's API drift in exchange for indirection nobody asked for. This is the LangChain failure mode — a horizontal abstraction layer over plumbing — and the reason M9 is "a format library", not "a framework".
- `pipeline.py`, `cli.py`, `sources/`, `extract/` are application code. They stay here.

**Entry criteria — all of them, or this is premature abstraction:**

1. M2 shipped and a real corpus of ≥100 documents has been ingested and re-ingested.
2. The frontmatter policy (§4.2) has gone one full milestone without a breaking change. Today it has never met a real document, so it will move.
3. A second consumer exists that is not this pipeline — the MCP server (M4) counts. One caller is not evidence of a reusable API.
4. `parse`/`render` round-trip holds on a bundle authored by something other than okragged.

**Note on reach.** For *consumption* the MCP server beats a library: it works from any language and any agent with no import, whereas a Python library only serves Python producers. M9 is about the production side and about not making the next OKF tool reimplement frontmatter parsing — it is not the distribution strategy for the format.

---

**M11 — grounded answers. Proposed.**

M4 made the bundle consumable, and M5 made it distributable. It did not make an *answer about* the bundle checkable, and those are different problems. `agent/AGENTS.md` says read `index.md` first, cite what you read, never write `verified` — but it is prose handed to a model, which makes every line of it a request. The bundle is verifiable; a sentence claiming to come from the bundle is not.

This is the same asymmetry §6.5 already resolved once. A model link error that can be repaired deterministically is repaired in `prune_links`, not asked for in the prompt, **because a repair is testable and a prompt rule is not**. M10 applies that to answers.

Three items, in dependency order:

1. **Report confidence, not rank.** `search` normalizes by the best hit, so the top result always scores `1.000`. Measured against the Spanish corpus: `"ley de gravedad de newton"` returns a cybercrime concept at `1.000`, indistinguishable from a correct query's top hit. A query with no term overlap does correctly return nothing, but any incidental overlap — `ley`, `argentina` — produces a perfect-looking score for an irrelevant concept. An agent handed `1.000` has no basis on which to abstain. Return the absolute score alongside the normalized one, and a floor below which `search` abstains with a reason instead of returning the least-bad match.

2. **Validate citations offline.** Given an answer and the concept ids it cites, check that each id exists in the bundle and that each quoted span actually occurs in the concept it is attributed to. No model call, no network — the `prune_links` pattern, applied one level up. `search` already returns pointers rather than bodies, so a grounded answer has to pass through `read_concept` and leaves a trail to check.

3. **Carry provenance into the hit.** Every result already knows its concept's `status` and trust tier (§4.4). Surfacing them lets an answer say it rests on a `draft` nobody has confirmed — which is the honest reading of most of a fresh bundle.

**Exit criterion.** On a question the bundle cannot answer, `search` abstains rather than returning a `1.000`-scored hit; and an answer citing a concept that does not exist, or quoting text absent from the concept it cites, is rejected by an offline check with no model call.

**Non-goals, and they matter here.** This does not stop a model answering from what it already knows — nothing in a retrieval design can, and pretending otherwise is how ungrounded answers get a trust tier they did not earn. It does not add a chat UI (§1). It does not settle retrieval strategy (§7). What it buys is narrower and worth more: when an answer *claims* the bundle as its source, that claim becomes checkable.
