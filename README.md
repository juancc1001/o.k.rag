# ragged

Turns a folder of files into a conformant **Open Knowledge Format (OKF) v0.2** bundle — a directory of markdown concepts with YAML frontmatter that agents and humans can both read, with no SDK required.

**Status: M2 done, plus M3's offline core.** A long source is planned into sections and each becomes its own concept under a parent, a second pass weaves cross-links into the prose, every directory gets an `index.md`, and a rebuild only touches the sources that changed. `ragged index` then embeds the bundle, projects it to 2D and writes a `graph.json` with both edge layers — the graph UI and the HTTP API that consume it are not built. The agent contract (M4) is next — see [Not implemented yet](#not-implemented-yet).

- Architecture, data model and rationale: [`DESIGN.md`](DESIGN.md)
- **Roadmap and milestones (M1–M5): [`DESIGN.md` §13](DESIGN.md#13-milestones)**

---

## Why a bundle *and* an embedding model

A vector database gives you semantic retrieval and nothing a human can read: the meaning lives in float arrays and what comes back is a chunk with no structure around it. A folder of markdown gives you the opposite — portable, readable, diffable — but retrievable only by grep and by whatever links someone remembered to write.

ragged is designed to have both, and what makes that possible is the **sidecar rule** ([`DESIGN.md` §2](DESIGN.md#2-the-three-layer-model)): nothing derived ever goes inside `bundle/`. Embeddings, the ANN index, the 2D projection and the content hashes all live in `.ragged/`, which is disposable and rebuildable. The bundle therefore stays something you can `tar` up and hand to someone who has never heard of ragged, while *also* being the corpus of a vector index — with no `embedding:` frontmatter key and no `.faiss` file anywhere near it. Swapping embedding providers is deleting `embeddings.npy` and re-embedding; `bundle/` does not notice.

What the vectors add on top of the markdown:

- **Node position** in the graph — proximity means semantic similarity rather than connectivity. **Computed** by `ragged index`.
- **Ghost edges** — pairs of concepts that are highly similar with *no* markdown link between them. This is the part links cannot give you: a missing link is not computable from the links you have. It is the lint signal saying two concepts are about the same thing and nobody connected them. **Computed** by `ragged index`; the browser view that makes them explorable is not built.
- **Semantic search** over concepts. Not wired up: `search` arrives with the MCP server (M4) and traverses the index and the links; embeddings upgrade it rather than enable it.

Embeddings sit deliberately outside the build path ([§6.8](DESIGN.md)). Concepts are produced, validated and consumed without them, which is why `build` never calls an embedding model and `index` is a separate command.

---

## Installation

Requires **Python 3.12+** (okflint requires it too).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

```bash
# macOS / Linux
python3 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
```

This installs the `ragged` console script plus `okflint` (the OKF linter) and `pytest`. Runtime dependencies: `anthropic`, `google-genai`, `markitdown[all]`, `numpy`, `pydantic`, `python-dotenv`, `pyyaml`, `typer`.

Two extras are deliberately *not* installed by default, because neither is needed to produce or validate a bundle and both are heavy:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[umap]"        # umap-learn; pulls numba
.\.venv\Scripts\python.exe -m pip install -e ".[fastembed]"   # local embeddings; ~150 MB of onnxruntime
```

Without `umap`, `ragged index` projects with PCA and says so. Without `fastembed`, the local embedding provider reports the extra rather than an import error.

`markitdown[all]` prints a `Couldn't find ffmpeg` warning on import. It only affects audio extraction and is safe to ignore.

Then add your API key:

```powershell
copy .env.example .env      # cp on macOS/Linux
# edit .env and fill in the key for your provider
```

Verify everything at once:

```powershell
.\.venv\Scripts\ragged doctor
.\.venv\Scripts\python.exe -m pytest tests -q
```

`doctor` reports the configured provider, whether a credential was found, whether the provider is reachable, and whether the configured model actually exists for your key — listing the alternatives if it doesn't.

### Providers

Two stages of `ragged build` call a model — synthesis and link-weave — and both use the same provider. Set which one in `ragged.toml`:

```toml
[synth]
provider = "gemini"     # gemini | anthropic
```

| Provider | Credential | Get a key | Default model |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `gemini-2.5-pro` |
| `anthropic` | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an `ant auth login` profile | [platform.claude.com](https://platform.claude.com/settings/keys) | `claude-opus-5` |

Both enforce the concept schema natively, so output shape is guaranteed rather than parsed out of prose — Anthropic via `output_config.format`, Gemini via `response_json_schema`. Provider modules live in `src/ragged/synth/providers/` and are thin: authentication, request shape, and decline detection. Adding a third means adding one module.

Two provider-specific notes:

- `[synth].effort` applies to **Anthropic only**. Gemini has no equivalent, so its thinking depth stays at the model default.
- Model IDs change. If `doctor` says your configured model is unavailable, set `[synth].model` to one of the names it lists.

`ragged index` has its own provider, set separately in `[vectors]`, because the embedding question is genuinely open ([`DESIGN.md` §12](DESIGN.md#12-risks-and-open-questions) Risk 4) and is meant to be settled by measuring ghost-edge quality on a real corpus:

| Provider | Credential | Model | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `gemini-embedding-001` | The default. Same key the build already needs, no extra dependency. |
| `fastembed` | none | `BAAI/bge-small-en-v1.5` | Local ONNX, offline, free to re-run. Optional extra — `pip install -e ".[fastembed]"`. |

Swapping between them is a config edit: `embeddings_meta.json` records which provider and model produced the matrix, so a change re-embeds rather than mixing two vector spaces in one file. `bundle/` is untouched either way.

Without a credential, `ragged build --dry-run`, `ragged extract`, and `ragged check` all still work — they never call a model. `ragged build` fails immediately with a pointer to `.env` rather than after extracting your whole corpus, and so does `ragged index`.

`.env` is gitignored; `.env.example` is the committed template.

---

## Usage

```powershell
ragged doctor                # config, credential, provider reachability, model availability
ragged build --dry-run       # extract + report only. No model calls, no writes.
ragged build                 # full pipeline -> bundle/ + okf-base.yaml
ragged index                 # embed + project + graph.json -> .ragged/ only
ragged check                 # validate an existing bundle without rebuilding
ragged extract <file>        # print extracted text for one file (offline, for debugging)
```

Typical first run:

```powershell
mkdir sources                            # drop your files here
copy .env.example .env                   # then add your key
.\.venv\Scripts\ragged doctor            # confirm the provider is reachable
.\.venv\Scripts\ragged build --dry-run   # confirm discovery + extraction
.\.venv\Scripts\ragged build
```

Start with two or three files. The first real run is where the synthesis prompt gets tuned. After that, a rebuild only pays for the sources whose bytes changed — a `build` over an untouched corpus makes no model calls at all.

### Exit codes

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `doctor` | provider reachable, model available | no credential, unreachable, or model unavailable | unknown provider name |
| `build` | all files synthesized, bundle conformant | a file failed, or the bundle is not conformant | no `sources/` directory, or no credential |
| `index` | every concept embedded, graph written | a concept failed to embed | no `bundle/` directory, or no credential |
| `check` | bundle conformant | not conformant | no `bundle/` directory |
| `extract` | printed | — | file not found |

---

## How it works

```
discover → extract → plan → synthesize → link-weave → emit → validate
   │          │         │         │            │         │        │
 hash      cached    sections  1 call      1 call     atomic   builtin
 files    by hash    cut in    per         per new     writes   checks +
   │                 code      section     concept              okflint
 unchanged? skip the whole row — no extraction, no model call
```

| Stage | What happens |
|---|---|
| **discover** | Walk `sources/` against the `include`/`exclude` globs. SHA-256 each file and diff against the ledger (`.ragged/manifest.json`). An unchanged file whose concepts are still on disk is **skipped entirely** — extraction and synthesis both. One exception: a file that lost a section to an error last run is re-ingested even unchanged, because a source recorded as finished is never retried until its bytes happen to change, and that section is content missing from the bundle. (A failed *weave* does not count — the concept exists and is valid, only its links are missing.) A file that has disappeared has its concepts marked `status: deprecated` rather than deleted; the spec keeps them for links and history. |
| **extract** | Bytes → markdown-ish text. `.md`/`.txt`/`.markdown` pass through (read as `utf-8-sig`); everything else goes through markitdown. Results are cached in `.ragged/extracted/<sha256>.json`, so re-runs cost nothing here. An extraction failure is recorded and reported — it does not abort the run. |
| **plan** | Only for sources over ~1200 words. One model call returns the parent concept's identity plus, per section, a **verbatim quote of its first words**. The text is then cut at those quotes *in code* — models quote reliably but do not count characters reliably. Matching is whitespace-insensitive, because extracted PDF text breaks lines mid-sentence. A quote that cannot be located is reported as a dropped section rather than silently losing text. Sections under ~150 words fold into their neighbour (§4.3). |
| **synthesize** | One model call per unit — the whole file when short, otherwise one per section, each with its own `max_tokens`. That per-section budget is the point: a single call returning an array makes every concept share one budget, which is what capped M1 at ~2.5k per concept no matter how long the source was. **Native schema enforcement** means the result is guaranteed rather than parsed out of prose. The system prompt is a frozen prefix (cached on Anthropic) and stays byte-identical across whole-document and per-section calls; section framing goes in the user message. A safety decline, a token-cap stop, or any non-2xx response (429 quota, 5xx, connection failure) becomes a per-unit error, never an aborted run — one failed section costs that section, not the document. |
| **link-weave** | Once every concept for the run exists, one model call per concept rewrites its body with cross-links woven into the prose. The schema returns the body and nothing else, so the pass cannot change a title, a type or a source. Links are bundle-absolute (`/finance/revenue-policy.md`) and inline — a link asserts a relationship and the sentence around it is what says which, so there is deliberately no "Related" section. A child is told its parent so it can link back (§4.3). Three things the model gets wrong are repaired mechanically rather than prompted against, all three found on a real corpus: an inverted `[/path.md](label)` is swapped back, a raw path used as the label becomes the target's title, and a target that is not a concept is unwrapped to plain text and reported — broken links are spec-legal when a human writes one forward, but a model holding the full index that emits a path outside it guessed. Only concepts this run wrote are woven, so an unchanged corpus still costs nothing. |
| **emit** | Assemble frontmatter and write the concept, then generate an `index.md` for **every** directory in the bundle, append to `log.md` under today's ISO date newest-first, and generate `okf-base.yaml`. Indexes are read off the bundle rather than accumulated during the run, so a directory whose concepts were all skipped is indexed like one just written, and every entry carries its target's own `title` and `description` — an index can never disagree with the concept it points at. An entry whose concept owns a subdirectory says how many concepts are below it and links to that subdirectory's index, so the root is a map rather than a dead end while disclosure stays progressive. Only the bundle root carries frontmatter (`E3`). Concepts the previous run produced for this source and this one did not are deleted; ids come from model-written titles, so a rebuild renames the tree routinely. A concept a human edited or `verified` is never overwritten — the replacement goes to `.ragged/proposed/` and is reported. All writes are atomic (temp file + rename), and the indexes, log and ledger are written on the way out even if the run is interrupted. |
| **validate** | Built-in conformance checks, then okflint as the normative gate. |

### The graph — `ragged index`

A separate command, because nothing in the build path uses a vector. `build` produces the bundle; `index` reads that bundle and writes four files into `.ragged/` and nothing anywhere else.

```
embed → project → graph.json
  │        │          │
one       UMAP     nodes + authored links + ghost edges
vector    (PCA
per       under 15
concept   concepts)
```

| Stage | What happens |
|---|---|
| **embed** | One vector per concept, over `title + description + body`. A concept longer than the provider's window is embedded as chunks and mean-pooled into a single vector — the chunk vectors are never stored, because the retrieval path is progressive disclosure, not chunk RAG. Vectors are L2-normalized on write, so cosine similarity is a dot product. A concept whose body still hashes to the recorded `embedded_body_sha256` is **not** re-embedded, so a second `index` over an unchanged bundle makes no API call. One concept failing is an error against that concept: it keeps whatever vector it had, the rest still land, and the stale hash makes the next run retry it. |
| **project** | Embeddings → 2D. UMAP with a fixed `random_state`, so the layout does not reshuffle on every run; PCA below 15 concepts (UMAP is unstable that small) or when `umap-learn` is not installed. The coordinates go to `layout.json` as well as into the graph. |
| **graph.json** | `nodes`, `links`, `ghosts`, `stats`. A **link** is an authored markdown cross-link that resolves to a concept. A **ghost** is a pair whose cosine similarity is at least τ (`ghost_threshold`, default 0.82), that is in each other's top-k neighbours (`knn_k`, default 8), and that has **no** markdown link between them in either direction — the work queue: two concepts about the same thing that nobody connected. Node `trust` is *derived* from the concept's `verified` block per the spec's tier rules and never written back. Every concept is a node, including one that failed to embed — it just has no position. |

τ is a guess until it meets a corpus ([`DESIGN.md` §12](DESIGN.md#12-risks-and-open-questions) Risk 1). Changing it and re-running `index` costs nothing: the vectors are reused and only the graph is recomputed.

### What gets written into frontmatter

```yaml
---
type: Policy                        # required by OKF; from the ragged.toml vocabulary
title: Revenue Recognition Policy
description: When revenue is recognized and how prepayments are deferred.
resource: file:///C:/.../sources/revenue-policy.md
tags: [finance]
sources:
  - resource: file:///C:/.../sources/revenue-policy.md
    id: revenue-policy              # footnote key used in the body
    title: revenue-policy
    last_modified: '2026-08-04'
generated:
  by: ragged/0.1.0
  at: '2026-08-04T04:29:07Z'
status: draft
x_ragged_source_hash: 92933ee5...   # producer extension; which source revision this is
---
```

**`verified` is never written.** That field records a confirmation event that actually happened; writing it because content looks correct would forge the human-reviewed trust tier. A human promotes a concept by editing the file. Concepts are emitted as `status: draft` and stay there until a human says otherwise.

New `type` values proposed by the model are **reported, never silently accepted** — they appear in the build summary so you can decide whether to add them to `ragged.toml`.

### Validation

Two layers, because neither alone is enough:

1. **Built-in checks** — always run, no dependencies. The three OKF conformance rules (frontmatter parses; `type` non-empty; reserved files well-formed) as errors `E1`/`E2`/`E3`, plus warnings `W1` (missing `title`/`description`), `W2` (broken cross-link), `W3` (no `generated.by`), `W4` (no `index.md` in a directory), `W5` (log dates not ISO or not newest-first).
2. **okflint** — the normative gate. Runs `okflint validate --manifest okf-base.yaml <bundle>`; exit 0 required.

Broken links are **warnings, not errors** — the spec explicitly permits them, since a link can point at knowledge not yet written.

`okf-base.yaml` is generated because okflint's `validate` always resolves a manifest and exits 2 without one. It is deliberately minimal — no `profile` block (that would reject newly proposed types, and required-field policy is the bundle owner's call) and no `okf_version` key (okflint 0.3.1 recognises only `0.1` and warns otherwise; the bundle-root `index.md` is where `0.2` is declared). If okflint is missing or the manifest is absent, validation reports *why* it was skipped rather than silently degrading.

---

## Important files

### Source

| File | Purpose |
|---|---|
| `src/ragged/cli.py` | Typer CLI: `build`, `check`, `extract`. Formatting and exit codes. |
| `src/ragged/pipeline.py` | Orchestrates the stages; assembles `Concept` objects from synthesis results. |
| `src/ragged/ledger.py` | The idempotency ledger, `.ragged/manifest.json`: what each source hashed to, which concepts it produced, and what ragged last wrote into each. Sidecar state — losing it costs a re-ingest, never correctness, so every read path degrades to "no record" rather than raising. Not `okf/manifest.py`, which is the *okflint* manifest. |
| `src/ragged/config.py` | Loads `ragged.toml` into pydantic models. Owns the `bundle/`, `sources/`, `.ragged/` path resolution. |
| `src/ragged/sources/base.py` | The `Source` protocol and `SourceItem`. Defines the `document` vs `asset` family split that phase-2 connectors plug into. |
| `src/ragged/sources/local.py` | `LocalFileSource` — the only implemented source. Globbing and hashing. |
| `src/ragged/extract/__init__.py` | Bytes → text, with the content-hash cache. markitdown plus passthrough. |
| `src/ragged/synth/prompt.py` | **The synthesis, planning and link-weave system prompts.** Each must stay byte-identical across its pass or prompt caching stops working; the only things that vary are the type vocabulary and the concept index, both snapshotted once per run. Tune concept quality here. |
| `src/ragged/synth/segment.py` | The planning pass and `cut` — marker matching, document-order sorting, short-section folding. `cut` is pure, so segmentation is testable without a model. |
| `src/ragged/synth/weave.py` | The link-weave pass and `prune_links` — unwrapping link targets that are not concepts. `prune_links` is pure, so the repair half is testable without a model. |
| `src/ragged/synth/__init__.py` | Provider dispatch plus everything shared: prompt assembly, JSON parsing, required-field checks, footnote repair. |
| `src/ragged/synth/base.py` | The concept and outline JSON schemas, the §4.3 word bounds, `SynthResult`, `SectionContext`, and the error types (`Refused`, `ProviderHTTPError`, `MissingCredential`, `SynthError`). |
| `src/ragged/synth/providers/claude.py` | Anthropic: `output_config.format`, cached system block, `stop_reason` handling. |
| `src/ragged/synth/providers/gemini.py` | Gemini: `response_json_schema`, `system_instruction`, `finish_reason` / `block_reason` handling. |
| `src/ragged/okf/concept.py` | The OKF data model. `render` / `parse` round-trip, unknown-key preservation, `slugify`. |
| `src/ragged/okf/inventory.py` | The format layer's read side: what is already in a bundle, as `(id, title, description, status)`. Feeds the prompt prefix, link-weave and the indexes. |
| `src/ragged/okf/writer.py` | Atomic concept writes, the per-directory `index.md` pass, `log.md` date-section insertion, deprecation and stale-concept removal. |
| `src/ragged/okf/validate.py` | Conformance checks and the okflint subprocess. |
| `src/ragged/okf/manifest.py` | Generates `okf-base.yaml`. Documents why each key is present or omitted. |
| `src/ragged/vectors/__init__.py` | `index(config)`: embed → project → graph, and the only place the three stages are wired together. |
| `src/ragged/vectors/embed.py` | One vector per concept: chunking, mean-pooling, hash-based reuse, per-concept failure isolation. |
| `src/ragged/vectors/store.py` | `embeddings.npy` + `embeddings_meta.json`. Sidecar state, so a read degrades to "nothing stored"; a provider or model change discards the matrix rather than mixing vector spaces. |
| `src/ragged/vectors/project.py` | UMAP with a fixed seed, PCA fallback in plain NumPy. Notes what Risk 5 would actually take to fix. |
| `src/ragged/vectors/knn.py` | Brute-force cosine kNN over the normalized matrix. Twenty lines, and enough until ~100k concepts. |
| `src/ragged/vectors/graph.py` | `graph.json`: the ghost-edge rule, derived trust tiers, node degree, stats. |
| `src/ragged/vectors/providers/gemini.py` | `gemini-embedding-001`. Same shape as a `synth` provider: `NAME`, `DEFAULT_MODEL`, `ENV_VARS`, `credential_present()`, `embed()`. |
| `src/ragged/vectors/providers/fastembed.py` | Local `bge-small-en-v1.5`, behind an optional extra. Registered but not chosen — see `DESIGN.md` §12 Risk 4. |

### Config and output

| Path | Committed? | What it is |
|---|---|---|
| `ragged.toml` | yes | Configuration. See below. |
| `.env.example` | yes | Template for the API key. Copy to `.env`. |
| `.env` | **no** (gitignored) | Your API key. Read only by `build` and `doctor`. |
| `sources/` | **no** (gitignored) | Layer 1 — raw input, yours to supply. **Immutable**; ragged reads, never writes. Not committed: a real corpus is your own material and tends to carry third-party names that do not belong in a permanent history. |
| `bundle/` | **no** (gitignored) | Layer 2 — the OKF product. The deliverable, portable with no ragged dependency — but concept ids derive from model-written titles, so every rebuild renames the whole tree and the diff is unreadable. Commit an example bundle under `examples/` instead, refreshed on purpose. |
| `okf-base.yaml` | **no** (gitignored) | Generated okflint manifest, rewritten with the bundle. |
| `.ragged/` | **no** (gitignored) | Sidecar: the ledger (`manifest.json`), the extraction cache, replacements for concepts a human owns (`proposed/`), and the vectors — `embeddings.npy`, `embeddings_meta.json`, `layout.json`, `graph.json`. Entirely derived and rebuildable — deleting it costs a full re-ingest and a re-embed, nothing more. |
| `DESIGN.md` | yes | Architecture, data model, risks, milestones. |

The separation matters: **nothing derived ever goes inside `bundle/`.** A bundle must stay something you can zip up, hand to someone who has never heard of ragged, and read with `cat`. That portability is the whole point of OKF, and an embeddings file or an `embedding:` frontmatter key would destroy it.

### `ragged.toml`

```toml
[bundle]
root = "bundle"            # output directory
okf_version = "0.2"        # written into the bundle-root index.md
actor = "ragged/0.1.0"     # written as generated.by
# title = "Derecho Informático"  # heading of the root index.md; omit for the
                           # project directory name, which names the tool

[sources.local]
family = "document"        # reserved; only "document" is implemented
path = "sources"
include = ["**/*.md", "**/*.pdf", "**/*.docx", "**/*.xlsx", "**/*.pptx", "**/*.html"]
exclude = ["**/~$*", "**/.DS_Store"]

[synth]
provider = "gemini"        # gemini | anthropic
# model = "gemini-2.5-pro" # omit for the provider default; `ragged doctor` lists what your key can reach
effort = "high"            # anthropic only: low | medium | high | xhigh | max
max_tokens = 16000

[types]
allowed = ["Document", "Concept", "Entity", "Process", "Decision", "Reference"]
allow_new = true           # new types are reported in the summary, never silent

[vectors]                  # ragged index only; build never reads these
provider = "gemini"        # gemini | fastembed (fastembed is an optional extra)
# model = "gemini-embedding-001"
dim = 768                  # requested output width; fastembed's 384 is fixed
knn_k = 8                  # a ghost edge must be in one end's top k
ghost_threshold = 0.82     # tau; a guess until it meets a corpus

[projection]
method = "umap"            # umap | pca; umap-learn is an optional extra
random_state = 42          # fixed, or the layout reshuffles every run
```

`type` is a free-form string in OKF and the spec forbids imposing a taxonomy, so this vocabulary is per-bundle consistency rather than a standard. If your corpus has a domain, replacing these defaults up front produces a much better bundle than discovering types file by file.

---

## Tests

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"   # so okflint is discoverable
.\.venv\Scripts\python.exe -m pytest tests -q
```

195 tests. No credentials and no network needed — the end-to-end test stubs the model and asserts the emitted bundle is conformant, so "one file in, one conformant concept out" is covered offline. The embedding provider is stubbed the same way, in a three-dimensional toy space, so what two concepts are "similar" is something the test states rather than something a model decides.

| File | Covers |
|---|---|
| `tests/test_okf.py` | render/parse round-trip, extension-key preservation, `E1`/`E2`/`E3`, link resolution (absolute and relative), log ordering and date merging |
| `tests/test_reingest.py` | the ledger: a second build over unchanged sources making zero model calls, what invalidates a skip (edited source, deleted bundle, corrupt or future-version ledger, a section that failed last run), deprecation of vanished sources, stale-output removal after a rename, id reservation across a partial rebuild, and human-edit/`verified` protection |
| `tests/test_pipeline.py` | end-to-end with a stubbed model, dry run, extraction-failure isolation, non-2xx isolation, interrupted-run finalization, id collisions, new-type reporting, footnote repair, and the segmented path: parent plus children, frontmatter-less child index, parents-only root index, one failed section vs all of them |
| `tests/test_segment.py` | `cut` against text whose lines break mid-sentence, unfound and out-of-order markers, short-section folding, the split threshold, and the outline call's required fields |
| `tests/test_index.py` | labels and descriptions taken from the target's frontmatter (accents intact), the bundle title from `[bundle].title`, the root marking a concept that owns a subtree, an index for every directory including one no concept owns, subtree counts, the deprecation marker, and indexes read off disk rather than accumulated |
| `tests/test_weave.py` | `prune_links` against all three repair modes plus self-links, relative links, URLs and footnotes; the prefix/user-message split that keeps caching working; the pass itself linking two concepts, telling each child its parent, recording the post-weave hash, surviving a failure, and skipping a concept a human owns |
| `tests/test_manifest.py` | manifest keys okflint requires, skip-with-reason behaviour, and cross-checking that okflint agrees with the built-in checks |
| `tests/test_providers.py` | provider registry and interface, dispatch, schema/required-field enforcement, Gemini credential resolution and decline mapping, non-2xx and transport-failure mapping for both providers, fail-fast pre-flight |
| `tests/test_vectors.py` | the embedding provider registry, hash-based reuse and per-concept failure isolation, mean-pooling past the window with no chunk vector stored, L2 normalization, a model change re-embedding instead of mixing widths, the PCA fallback and its determinism, the ghost rule (τ, top-k, a link in either direction), derived trust tiers, graph stats, and that `index` writes nothing into `bundle/` |
| `tests/test_env.py` | `.env` loading with and without a BOM, and every `doctor` outcome |
| `tests/test_encoding.py` | the [encoding policy](#encoding-policy), enforced by AST scan (including the child-process `env` rule), plus BOM tolerance, the piped-child crash reproduced, and LF-only output end to end |

Tests that need okflint skip cleanly when it is not on `PATH`.

---

## Platform notes

## Encoding policy

Five separate bugs shipped during M1, all the same defect: **trusting a platform default at an I/O boundary.** Windows defaults to cp1252, not UTF-8. So the rule is absolute, with no per-call-site judgement:

| Boundary | Rule | Why |
|---|---|---|
| Reading text | `encoding="utf-8-sig"` | Tolerates a BOM; byte-identical without one. Notepad, VS Code and Excel all write BOMs. |
| Writing text | `encoding="utf-8"`, `newline="\n"` | Byte-stable across platforms, never a BOM |
| Subprocesses (our decode) | `encoding="utf-8", errors="replace"` | `text=True` inherits the locale codepage |
| Subprocesses (their encode) | `PYTHONIOENCODING=utf-8` in `env` | The child's stdout is a pipe, so its own Python picks the codepage |
| stdout / stderr | reconfigured to UTF-8 at CLI entry | The default is the codepage, so redirecting output crashes |
| git | `.gitattributes` with `eol=lf` | Otherwise checkout rewrites the bundle to CRLF |

**`tests/test_encoding.py` enforces this statically** — it walks the AST of everything under `src/ragged/` and fails the build if any read, write, or subprocess call deviates. It also contains a meta-test pointing the check at a deliberately bad file, because a guard that always passes guards nothing.

The five bugs, for reference:

1. **`.env`** — python-dotenv reads UTF-8 by default, so a BOM produced a key literally named `﻿GEMINI_API_KEY`. Silent: `.env` reported "found" while the credential reported "missing".
2. **Passthrough extraction** — a BOM rode into the synthesis prompt as a leading invisible character.
3. **okflint subprocess** — `text=True` decoded with cp1252, okflint emits characters outside it, the decode raised inside the reader thread and `stdout` came back as `None`.
4. **Redirected stdout** — `ragged extract x.pdf > out.txt` raised `UnicodeEncodeError` on any character outside cp1252. Deceptive because `acción` and `€` *are* in cp1252, so it only broke on `≥`, `—`, `≈` and curly quotes — exactly what a model writes into titles and descriptions.

5. **okflint's own stdout** — fixing #3 only fixed *our* decode. okflint's stdout is a pipe, so its Python chose cp1252 and raised `UnicodeEncodeError` inside its own `print()` while reporting a finding containing `—`. It died mid-report, so its exit code was non-zero and `ragged build` declared a perfectly conformant bundle "NOT conformant: 0 error(s)". The fix is `PYTHONIOENCODING=utf-8` in the child's `env`.

That fourth one was masked in development: Claude Code sets `PYTHONIOENCODING=utf-8:surrogateescape`, so it could not reproduce until the variable was cleared. If you are testing terminal behaviour, check what your environment sets first.

---

## Not implemented yet

Full roadmap with exit criteria in [`DESIGN.md` §13](DESIGN.md#13-milestones).

### M2 — done

All four items in [`DESIGN.md` §13](DESIGN.md#13-milestones):

- **Segmentation** (§4.3). A source over ~1200 words is planned into sections and each becomes its own concept under a parent. Measured on a 3-PDF corpus of 86k characters, retention went from 9% of the source to 48%.
- **Incremental re-ingest** (§5, §6.1). A `build` over an untouched corpus makes zero model calls; segmentation had multiplied the cost of not having this, since one source is N+1 calls rather than 1. Also deprecation of vanished sources, stale-output cleanup after a rename, and human-edit protection.
- **Link weaving** (§6.5). The bundle is a graph rather than a set of small trees. Note that links point forward from concepts written this run to concepts that already existed, and not back — a skipped source's concepts are not rewritten, because that would spend a model call producing a file nobody asked for. The missing direction is what M3's ghost edges are for.
- **Per-directory `index.md`** (§6.6), read off the bundle rather than accumulated during the run.

### M3 — offline core done

`ragged index` writes `embeddings.npy`, `embeddings_meta.json`, `layout.json` and `graph.json`, all into `.ragged/`. The embedding provider is pluggable and **deliberately undecided** ([`DESIGN.md` §12](DESIGN.md#12-risks-and-open-questions) Risk 4): `gemini` and `fastembed` are both registered, the choice is config, and it is meant to be settled by measuring ghost-edge quality on a real corpus rather than argued up front.

### Next

- **MCP server + generated `AGENTS.md`** (M4) — how a reading session consumes the bundle with no credential. This is the point of the project.
- **The rest of M3**: the FastAPI app and the sigma.js graph view with the τ slider. `graph.json` exists but nothing renders it yet, so τ = 0.82 is still a guess.
- **BigQuery/blob sources.** M5.

### Known rough edges in segmentation

- **Concept ids get long.** They come from model-written titles, so a parent plus child path can reach ~150 characters. Under Windows `MAX_PATH` that is tight but currently works; deeper nesting or longer titles would not.
- **Section titles occasionally drift language** on a Spanish corpus. Prompt calibration is deliberately post-M2 (`DESIGN.md` §13) because few-shot examples should come from a human-promoted `stable` concept, not from a guess.
- **okflint still reports `S202` split candidates** on some children. Segmentation cut the documents; it did not cut them as finely as okflint's cohesion clustering would.
- **A footnote label can resolve to nothing.** A generated concept was observed carrying `[^ley-26388]` while its `sources` declared only `id: delitos-2`. Footnote labels are the join key into `sources` (§5.1), so a label matching no declared id silently breaks per-claim attribution. `ensure_footnote_defs` only checks that a referenced footnote has a *definition*, not that its label is a declared source id, and no `W`-code covers it — okflint passes such a bundle. Worth a check in both `synth` and `okf/validate.py`.
