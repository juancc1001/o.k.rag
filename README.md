# okragged

Turns a folder of files into a conformant **Open Knowledge Format (OKF) v0.2** bundle — a directory of markdown concepts with YAML frontmatter that agents and humans can both read, with no SDK required.

**Status: M1–M4 done; M5–M7 in progress with their implementations built and only real-world exit criteria (a real PyPI publish, a session from a truly foreign checkout) still open; M8 done.** A long source is planned into sections, and each becomes its own concept under a parent. A second pass weaves cross-links into the prose, every directory gets an `index.md`, and a rebuild only touches the sources that changed. `okragged index` then embeds the bundle, projects it to 2D, and writes a `graph.json` with both edge layers, while `okragged serve` renders it — a sigma.js map where proximity represents semantic similarity and a **ghost edge** connects a pair that embeddings indicate are related but nobody linked. Every build generates the **agent contract** in `agent/AGENTS.md`; the packaged MCP server exposes `search`, `read_concept`, `add_source`, `lint`, and — on a hosted, OAuth-authenticated instance configured to offer it — `verify_concept`, none of which *requires* a credential to call. Over stdio the server is read-only unless launched otherwise, and reads no `.env` file unless requested; over `--transport http` every request is authenticated as an OAuth 2.1 resource server, and writing or spending additionally needs the scopes and rate limit configured for it (see [Hosting over http](#hosting-over-http--okragged-mcp---transport-http)). See [Roadmap and open work](#roadmap-and-open-work).

- Architecture, data model and rationale: [`DESIGN.md`](DESIGN.md)
- **Roadmap and milestones (M1–M11): [`DESIGN.md` §13](DESIGN.md#13-milestones)**

---

## Why a bundle *and* an embedding model

A vector database gives you semantic retrieval and nothing a human can read: the meaning lives in float arrays and what comes back is a chunk with no structure around it. A folder of markdown gives you the opposite — portable, readable, diffable — but retrievable only by grep and by whatever links someone remembered to write.

okragged is designed to have both, and what makes that possible is the **sidecar rule** ([`DESIGN.md` §2](DESIGN.md#2-the-three-layer-model)): nothing derived ever goes inside `bundle/`. Embeddings, the ANN index, the 2D projection and the content hashes all live in `.okragged/`, which is disposable and rebuildable. The bundle therefore stays something you can `tar` up and hand to someone who has never heard of okragged, while *also* being the corpus of a vector index — with no `embedding:` frontmatter key and no `.faiss` file anywhere near it. Swapping embedding providers is deleting `embeddings.npy` and re-embedding; `bundle/` does not notice.

What the vectors add on top of the markdown:

- **Node position** in the graph — proximity means semantic similarity rather than connectivity. **Computed** by `okragged index`.
- **Ghost edges** — pairs of concepts that are highly similar but have *no* markdown link between them. Links alone cannot reveal a missing link. A ghost edge is the lint signal that two concepts may concern the same subject without being connected. Ghost edges are **computed** by `okragged index`, shown in the view mounted by `okragged serve`, and reported by the `lint` tool.
- **Semantic search** over concepts. The MCP server's `search` works without them by traversing the indexes and the links; embeddings upgrade that path rather than enable it, and only when a credential is present to embed the query with.

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

This installs the `okragged` console script plus `okflint` (the OKF linter), `pytest`, and the MCP SDK — the last two so the suite runs whole rather than skipping its server tests. Runtime dependencies: `anthropic`, `google-genai`, `markitdown[all]`, `numpy`, `pydantic`, `python-dotenv`, `pyyaml`, `typer`.

Four extras are deliberately *not* installed by default, because none is needed to produce or validate a bundle:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[umap]"        # umap-learn; pulls numba
.\.venv\Scripts\python.exe -m pip install -e ".[fastembed]"   # local embeddings; ~150 MB of onnxruntime
.\.venv\Scripts\python.exe -m pip install -e ".[mcp]"         # the MCP SDK; pulls starlette + uvicorn
.\.venv\Scripts\python.exe -m pip install -e ".[api]"         # FastAPI + uvicorn, for `okragged serve`
```

Without `umap`, `okragged index` projects with PCA and says so. Without `fastembed`, the local embedding provider reports the extra rather than an import error. Without `mcp`, everything except starting the MCP server still works — `okragged agent` still generates the contract, and the server reports the extra when it is launched. Without `api`, `okragged serve` reports the extra instead of starting.

The graph view is built separately and needs Node 20+ — see [The graph view](#the-graph-view--okragged-serve) for the five commands that put it on screen.

`markitdown[all]` prints a `Couldn't find ffmpeg` warning on import. It only affects audio extraction and is safe to ignore.

Then add your API key:

```powershell
copy .env.example .env      # cp on macOS/Linux
# edit .env and fill in the key for your provider
```

Verify everything at once:

```powershell
.\.venv\Scripts\okragged doctor
.\.venv\Scripts\python.exe -m pytest tests -q
```

`doctor` reports the configured provider, whether a credential was found, whether the provider is reachable, and whether the configured model actually exists for your key — listing the alternatives if it doesn't.

### Providers

Two stages of `okragged build` call a model — synthesis and link-weave — and both use the same provider. Set which one in `okragged.toml`:

```toml
[synth]
provider = "gemini"     # gemini | anthropic
```

| Provider | Credential | Get a key | Default model |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `gemini-2.5-pro` |
| `anthropic` | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an `ant auth login` profile | [platform.claude.com](https://platform.claude.com/settings/keys) | `claude-opus-5` |

Both enforce the concept schema natively, so output shape is guaranteed rather than parsed out of prose — Anthropic via `output_config.format`, Gemini via `response_json_schema`. Provider modules live in `src/okragged/synth/providers/` and are thin: authentication, request shape, and decline detection. Adding a third means adding one module.

Two provider-specific notes:

- `[synth].effort` applies to **Anthropic only**. Gemini has no equivalent, so its thinking depth stays at the model default.
- Model IDs change. If `doctor` says your configured model is unavailable, set `[synth].model` to one of the names it lists.

`okragged index` has its own provider, set separately in `[vectors]`, because the embedding question is genuinely open ([`DESIGN.md` §12](DESIGN.md#12-risks-and-open-questions) Risk 4) and is meant to be settled by measuring ghost-edge quality on a real corpus:

| Provider | Credential | Model | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `gemini-embedding-001` | The default. Same key the build already needs, no extra dependency. |
| `fastembed` | none | `BAAI/bge-small-en-v1.5` | Local ONNX, offline, free to re-run. Optional extra — `pip install -e ".[fastembed]"`. |

Swapping between them is a config edit: `embeddings_meta.json` records which provider and model produced the matrix, so a change re-embeds rather than mixing two vector spaces in one file. `bundle/` is untouched either way.

### Credentials

**There are two, and they are configured separately.** `[synth]` is the writing provider; `[vectors]` is the embedding provider. They exist independently because the embedding question is open (Risk 4) and because nothing in the build path uses a vector — so a missing embedding key says nothing about whether you can build, and vice versa. They happen to default to the same Gemini key, which is why one `GEMINI_API_KEY` usually covers both.

**What needs which:**

| | `[synth]` | `[vectors]` |
|---|---|---|
| `build` | **required** | — |
| `index` | — | **required** |
| `search` — embedding path | — | required, per query |
| `search` — traversal fallback | — | — |
| `build --dry-run`, `extract`, `check`, `agent`, `serve` | — | — |
| `read_concept`, `lint` | — | — |
| `add_source` recording | — | — |
| `add_source` synthesizing (`mcp --write --spend`) | **required** | for the vector, or the concept is unranked |
| `mcp` without `--spend` | — | — |
| `mcp --spend` | for `add_source` | the embedding path, and the vector a write leaves behind |

**No tool on the MCP surface *requires* a credential, `add_source` included.** Recording prose calls no model, so the whole surface answers for a consumer holding somebody else's bundle and no key of their own — which is what M4 is for. Over stdio there is nobody to charge: MCP has no identity, the server being a subprocess of whatever client spawned it, so "whose key pays" would otherwise be settled by an accident of environment precedence.

**And `okragged mcp` no longer reads your `.env` unless you ask it to.** The server used to load the project's `.env` merely for being pointed at that directory, and what that buys is the *embedding* key `search` uses to embed a query — so a consumer's search spent the producer's quota. Reads cost money even where writes do not. Two switches, both off by default, and neither reads an environment variable, because a mode a stray variable could turn on is the ambient authority this removes:

| invocation | the corpus | your credentials |
|---|---|---|
| `okragged mcp` | read-only; `add_source` is absent, not present-and-refusing | unread — `search` traverses the index and says so |
| `--spend` | read-only | read; `search` may embed the query |
| `--write` | `add_source` records prose for the next `okragged build` | unread |
| `--write --spend` | `add_source` records prose, **authors its concept, and embeds it** so `search` can rank it | read; a `[synth]` call for the concept and a `[vectors]` one for its vector |

**Over stdio, what stands behind the spending mode is that you launched it, and nothing else.** There is no identity and no rate limit: a caller who owns the process could present any token he was asked for and delete any counter kept beside the bundle. So `--write --spend` there is for an instance *you* run for *your* agent, where the caller and the payer are the same person; it is not a way to let somebody else spend your key on terms.

**Over `--transport http`, that changes** — see [Hosting over http](#hosting-over-http--okragged-mcp---transport-http) below, which is where a channel with someone else on the far end exists at all and where [§13 M8 criterion 4](DESIGN.md#13-milestones) binds an identity and a rate limit to it.

`build` **fails its pre-flight before doing work** — it refuses before extracting your corpus rather than after, so a missing key never leaves debris.

**Where each entry point looks:**

| Entry point | Reads |
|---|---|
| `okragged <command>` | `.env` beside `okragged.toml`, then the environment |
| `okragged mcp --project X` | `X/.env`, then the environment |
| the Python API | **nothing** — see below |

The library deliberately reads no `.env`. `okragged` the command loads one because a human typed the command; a function call has no business going looking for a dotfile in whatever directory it was invoked from. Put the key in the environment, or hand `build` a `Config` for a project whose keys you loaded yourself.

**For a consumer registering the server against someone else's bundle**, supply nothing at all — no `env` block, no key:

```json
{"mcpServers": {"okragged": {
  "command": "okragged",
  "args": ["mcp", "--project", "/path/to/the/bundle"]
}}}
```

Every tool that instance offers works that way. `search` answers by traversal instead of by embedding kNN and says which path answered. Add `--write` to the `args` if that instance should also offer `add_source`, and `--spend` if it should read the project's `.env`. Those two together turn `add_source` into one that authors the concept as well, which is why they belong to an instance you run for your own agent rather than to one you registered against somebody else's directory — `X/.env` is the *producer's* file, and may be neither present on your machine nor yours to use. Put your own key in this entry's `env` instead.

`.env` is gitignored; `.env.example` is the committed template. `okragged doctor` reports both providers, whether each credential was found, and whether the configured model exists for your key.

---

## Usage

```powershell
okragged doctor                # both providers, credentials, reachability, model availability
okragged build --dry-run       # extract + report only. No model calls, no writes.
okragged build                 # full pipeline -> bundle/ + okf-base.yaml
okragged index                 # embed + project + graph.json -> .okragged/ only
okragged build-view            # build the graph view from web/ (checkout only)
okragged serve                 # the read-only API, and the graph view if it is built
okragged serve --all           # ... building the view first if it is missing or stale
okragged check                 # validate an existing bundle without rebuilding
okragged agent                 # regenerate agent/AGENTS.md (offline)
okragged agent --register      # also register the server in .mcp.json, read-only
okragged agent --register --write --spend   # ... as a writing, spending instance
okragged mcp --project <dir>   # serve a bundle to an MCP client over stdio, read-only
okragged mcp --write --spend   # ... offering add_source, on your credential
okragged extract <file>        # print extracted text for one file (offline, for debugging)
```

Each of these is a function too — see [Driving it from Python](#driving-it-from-python).

Typical first run:

```powershell
mkdir sources                            # drop your files here
copy .env.example .env                   # then add your key
.\.venv\Scripts\okragged doctor            # confirm the provider is reachable
.\.venv\Scripts\okragged build --dry-run   # confirm discovery + extraction
.\.venv\Scripts\okragged build
```

Start with two or three files. The first real run is where the synthesis prompt gets tuned. After that, a rebuild only pays for the sources whose bytes changed — a `build` over an untouched corpus makes no model calls at all.

### Exit codes

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `doctor` | provider reachable, model available | no credential, unreachable, or model unavailable | unknown provider name |
| `build` | all files synthesized, bundle conformant | a file failed, or the bundle is not conformant | no `sources/` directory, no credential, or another writer holds the project |
| `index` | every concept embedded, graph written | a concept failed to embed | no `bundle/` directory, no credential, or another writer holds the project |
| `build-view` | view built, or `web/` absent (installed wheel) | `npm install`/`npm run build` failed | — |
| `serve` | — (runs until interrupted) | — | no `bundle/` directory, or the `api` extra is missing |
| `check` | bundle conformant | not conformant | no `bundle/` directory |
| `agent` | contract written | — | no `bundle/` directory |
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
| **discover** | Walk `sources/` against the `include`/`exclude` globs. SHA-256 each file and diff against the ledger (`.okragged/manifest.json`). An unchanged file whose concepts are still on disk is **skipped entirely** — extraction and synthesis both. One exception: a file that lost a section to an error last run is re-ingested even unchanged, because a source recorded as finished is never retried until its bytes happen to change, and that section is content missing from the bundle. (A failed *weave* does not count — the concept exists and is valid, only its links are missing.) A file that has disappeared has its concepts marked `status: deprecated` rather than deleted; the spec keeps them for links and history. |
| **extract** | Bytes → markdown-ish text. `.md`/`.txt`/`.markdown` pass through (read as `utf-8-sig`); everything else goes through markitdown. Results are cached in `.okragged/extracted/<sha256>.json`, so re-runs cost nothing here. An extraction failure is recorded and reported — it does not abort the run. |
| **plan** | Only for sources over ~1200 words. One model call returns the parent concept's identity plus, per section, a **verbatim quote of its first words**. The text is then cut at those quotes *in code* — models quote reliably but do not count characters reliably. Matching is whitespace-insensitive, because extracted PDF text breaks lines mid-sentence. A quote that cannot be located is reported as a dropped section rather than silently losing text. Sections under ~150 words fold into their neighbour (§4.3). |
| **synthesize** | One model call per unit — the whole file when short, otherwise one per section, each with its own `max_tokens`. That per-section budget is the point: a single call returning an array makes every concept share one budget, which is what capped M1 at ~2.5k per concept no matter how long the source was. **Native schema enforcement** means the result is guaranteed rather than parsed out of prose. The system prompt is a frozen prefix (cached on Anthropic) and stays byte-identical across whole-document and per-section calls; section framing goes in the user message. A safety decline, a token-cap stop, or any non-2xx response (429 quota, 5xx, connection failure) becomes a per-unit error, never an aborted run — one failed section costs that section, not the document. |
| **link-weave** | Once every concept for the run exists, one model call per concept rewrites its body with cross-links woven into the prose. The schema returns the body and nothing else, so the pass cannot change a title, a type or a source. Links are bundle-absolute (`/finance/revenue-policy.md`) and inline — a link asserts a relationship and the sentence around it is what says which, so there is deliberately no "Related" section. A child is told its parent so it can link back (§4.3). Three things the model gets wrong are repaired mechanically rather than prompted against, all three found on a real corpus: an inverted `[/path.md](label)` is swapped back, a raw path used as the label becomes the target's title, and a target that is not a concept is unwrapped to plain text and reported — broken links are spec-legal when a human writes one forward, but a model holding the full index that emits a path outside it guessed. Only concepts this run wrote are woven, so an unchanged corpus still costs nothing. |
| **emit** | Assemble frontmatter and write the concept, then generate an `index.md` for **every** directory in the bundle, append to `log.md` under today's ISO date newest-first, and generate `okf-base.yaml`. Indexes are read off the bundle rather than accumulated during the run, so a directory whose concepts were all skipped is indexed like one just written, and every entry carries its target's own `title` and `description` — an index can never disagree with the concept it points at. An entry whose concept owns a subdirectory says how many concepts are below it and links to that subdirectory's index, so the root is a map rather than a dead end while disclosure stays progressive. Only the bundle root carries frontmatter (`E3`). Concepts the previous run produced for this source and this one did not are deleted; ids come from model-written titles, so a rebuild renames the tree routinely. A concept a human edited or `verified` is never overwritten — the replacement goes to `.okragged/proposed/` and is reported. Writes are atomic per file (temp + rename), and each **source** commits as a unit: its concepts go to `.okragged/staging/`, and one fenced commit publishes them together with the removal of the stale output its rename left behind and its own ledger entry. So an interrupted run keeps every source it finished, and only the one in flight is redone. The indexes, the log and `okf-base.yaml` are written on the way out and are derived from the bundle, so any later run regenerates them. See [One writer, and a commit per source](#one-writer-and-a-commit-per-source). |
| **validate** | Built-in conformance checks, then okflint as the normative gate. |

### The graph — `okragged index`

A separate command, because nothing in the build path uses a vector. `build` produces the bundle; `index` reads that bundle and writes four files into `.okragged/` and nothing anywhere else.

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

**τ has now met a corpus, and the default lost** ([`DESIGN.md` §12](DESIGN.md#12-risks-and-open-questions) Risk 1). On the 3-PDF Spanish corpus the median similarity across all 153 concept pairs is **0.896**, and 150 of them clear τ = 0.82 — so the threshold excludes almost nothing and the 76 ghost edges are 76 because `knn_k = 8` capped them, not because τ did. That is the predicted failure mode of a single-topic corpus, and it does not by itself say whether the fix is a higher τ, a threshold relative to the corpus's own distribution, or a smaller k. The default has deliberately not moved on one sample. Changing it and re-running `index` costs nothing: the vectors are reused and only the graph is recomputed.

### The graph view — `okragged serve`

**Running it, from nothing, once:**

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[api]"   # FastAPI + uvicorn
.\.venv\Scripts\okragged index                            # writes .okragged/graph.json
cd web
npm install                                             # needs Node 20+
npm run build                                           # -> src/okragged/api/static/
cd ..
.\.venv\Scripts\okragged serve                            # http://127.0.0.1:8000
```

Or, in a checkout, one command instead of the middle three: `okragged serve --all` builds the view first if it is missing or stale (built `index.html` absent, or older than anything under `web/src/`, `web/index.html`, `web/vite.config.ts` or `web/package.json`), then serves as above. `okragged build-view` does just the build half, for when you want the view refreshed without also starting the server. Both need `web/`, which only exists in a checkout — an installed wheel ships `api/static/` prebuilt and has no `web/` to build, so both report that plainly rather than failing.

Then open **<http://127.0.0.1:8000>**. Ghost edges are off by default — tick **show ghost edges** in the left panel to see them, and drag **τ** to decide how similar a pair has to be before the graph claims they belong together.

Three of those five steps are one-time setup. After that, run `okragged serve` (or `okragged serve --all`, which only rebuilds the view when it is actually stale) day to day, and run `okragged index` whenever the bundle changes. The view reads `graph.json` and nothing else, so it shows whatever the last `index` computed.

**While working on the view itself**, run Vite instead, so a save reloads the page:

```powershell
.\.venv\Scripts\okragged serve      # terminal 1 — the API on :8000
cd web; npm run dev               # terminal 2 — the view on :5173, proxying /api
```

Open **<http://127.0.0.1:5173>** for that one. `okragged serve` also takes `--host` and `--port`.

**If something is missing, the command says which:**

| what you see | what to run |
|---|---|
| `the HTTP API needs the optional extra` | `pip install -e ".[api]"` |
| `no graph.json - /api/graph will 404` | `okragged index` |
| `no built view - API only` | `npm install && npm run build` in `web/`, or `okragged build-view` |
| `no bundle at bundle/` | `okragged build` |

The view is optional; the endpoints are not. `okragged serve` runs the API with or without the built view, and [`/api/docs`](http://127.0.0.1:8000/api/docs) is browsable either way.

Four read-only endpoints, all offline, none of which writes anywhere:

| | | |
|---|---|---|
| `GET /api/graph` | `graph.json` as `okragged index` wrote it | 404 until it has run |
| `GET /api/concept/{id}` | frontmatter, body, and the body rendered | 404 for anything outside the bundle |
| `GET /api/search?q=&k=` | the same two paths as the MCP tool | pointers, never bodies |
| `GET /api/lint` | the same report as the MCP tool | conformance, ghosts, stale, orphans |

`search` and `lint` call the same functions `agent/` exposes over MCP, so the view and an agent cannot be told different things about one bundle. `POST /api/ingest` and its SSE progress stream ([`DESIGN.md` §11](DESIGN.md#11-http-api-and-config)) are deferred — they need a job model, and writing stays on the CLI and the MCP tool until something asks for it over HTTP. The progress events they also need [now exist](#driving-it-from-python).

The view itself is sigma.js v3 + graphology over WebGL, in `web/`. Positions are precomputed by UMAP, so nothing simulates a layout in the browser and the map is identical on every load. Node colour is `type`, size is link degree, border is the trust tier, and a faded node is deprecated or past its `stale_after`. Solid edges are authored links; **ghost edges are off by default and the τ slider is the instrument** — it filters upward from the τ `graph.json` was computed at, because the file holds no pair below it. Selecting a node fetches its concept into a side panel. Raw HTML in a body is escaped rather than rendered: the body is model-written.

### The agent contract — `agent/` and `.mcp.json`

Layer 3 is two files with two different lifecycles, and only one of them is written by a build:

```
agent/AGENTS.md        how to read this bundle: structure, type vocabulary,
                       trust tiers, the four operations
                       -> regenerated at the end of every build
.mcp.json              the registration entry, merged into whatever else
                       is already registered
                       -> written only by `okragged agent --register`
```

`AGENTS.md` is written *beside* `bundle/`, never inside it, and it is **derived from the bundle** — concept counts, the structure tree, the type vocabulary — so it is regenerated after every commit. That is what keeps it from describing a bundle that no longer exists.

`.mcp.json` is **not derived from the bundle**. The entry is the same bytes whether the bundle holds no concepts or a thousand, so there is nothing for a rebuild to refresh. More to the point, it is not okragged's file: it is the MCP config for whatever agent works in that directory, shared with every other server registered in it. So a build never writes it — it only mentions when the server is unregistered — and `okragged agent --register` is how you ask. For the same reason it is **not gitignored**: whether your MCP config belongs in your history is your call.

**By default the entry it writes is `okragged mcp` with no switches, which is the read-only instance**, and `--register` says what it granted when it writes it: no `add_source`, and `search` ranking by traversal rather than by embedding your query. Those bytes did not change when the switches arrived — their meaning narrowed — so registering for your own use and finding the writing tool gone would otherwise have nothing to explain it.

**`--register` takes the switches too**, and they land in the entry's `args`:

```powershell
okragged agent --register --write --spend   # -> args: ["mcp", "--write", "--spend"]
```

Nothing is inferred here — typing that is asking for that entry specifically, which is the same "only when asked" rule the file has always had. Omitted, the entry stays bare. On their own the two switches are refused rather than ignored: they describe the entry `--register` writes, so without it they describe nothing.

The entry in `AGENTS.md` for a *foreign* project stays flagless whatever you registered locally. What you authorized on your machine, with your key, is not a fact about somebody else's — a snippet carrying `--spend` would be telling a stranger to spend a key.

The server is `okragged mcp`, a subcommand of the console script — there is no generated launcher to point at, so the entry is portable:

```json
{ "mcpServers": { "okragged": { "command": "okragged", "args": ["mcp"] } } }
```

No interpreter path and no script path, which is what makes any MCP-capable agent a consumer of the bundle with no okragged-specific code **from any tree at all**. The server takes its bundle from `--project` or `OKRAGGED_PROJECT`, defaulting to the cwd — a client spawns it with the config file's directory as its cwd, so the entry beside the bundle needs no target. To consume the bundle from *another* project, paste the entry `AGENTS.md` prints instead: the same two words plus `--project` and this root, absolutely.

**The bare name is used only when `PATH` actually resolves it.** A client spawns the server from `.mcp.json`, never from an activated shell, so an `okragged` installed into a venv is not on the spawn's `PATH` — the process dies before it says anything and the client reports `CONNECTION_CLOSED` with nothing to read anywhere. `register` therefore falls back to the console script beside the running interpreter, which is where that install sits. This has the same exposure as okflint's resolution below, but with a worse symptom: okflint degrades to "not installed", while this loses the whole server. `PATH` is tried first here rather than second because this file is not gitignored: a bare name travels to another checkout and an absolute one does not. If you would rather the entry stay portable, put okragged on your `PATH` and re-run `okragged agent --register`. For a published package, use `pipx install okragged`, which requires the distribution milestone first. From a checkout today, use `pipx install --editable <root>`; this exposes the console script without putting okragged's dependencies into shared site-packages.

Four tools always, plus a fifth on the right instance: `add_source` is offered only when started with `--write`, and `verify_concept` only under `--transport http` with `[mcp].verify_scopes` configured ([Hosting over http](#hosting-over-http--okragged-mcp---transport-http)):

| Tool | Signature | Credential |
|---|---|---|
| `search` | `(query, k=8)` → `path`, `note`, and pointers: `concept_id`, `title`, `description`, `score` | none |
| `read_concept` | `(concept_id)` → the raw file, frontmatter included | none |
| `add_source` | `(text, title=null)` → the source written, whether it was recorded, the concepts authored if the instance synthesizes, and whether they were indexed | none to record, `[synth]` to synthesize, `[vectors]` to index |
| `lint` | `()` → conformance, ghost edges, stale concepts, orphans | none |
| `verify_concept` | `(concept_id)` → the appended `{by, at}` entry and the concept's resulting trust tier | none — it authenticates the caller, it does not spend anyone's key |

The ghost list is capped at the 20 strongest, with `ghosts_total` and a note saying what was left out — they grow with the square of the corpus and this is a tool result an agent pays for by the token. `lint` also says when `graph.json` no longer describes the bundle, which a rebuild makes routine: ids come from model-written titles, so a rename means the old graph is about *different* concepts, not merely stale ones.

**`search` returns pointers and never bodies**, because the retrieval path here is progressive disclosure rather than chunk RAG: the agent reads `index.md`, then reads whole concept files, and `AGENTS.md` says so explicitly.

**Every tool works with no credential at all**, and the one that writes only ever adds — `ingest` came off this surface in M5, so nothing reachable over the protocol can sweep the corpus, in any mode. That is what M4 is for — the bundle has to be readable by a session that has no key. That constrains `search`, since concept vectors are computed at build time but embedding a *query* is a read-time model call:

- `embeddings.npy` present **and** an embedding credential present → kNN over the stored matrix, as `path: embeddings`.
- otherwise → **index traversal**, as `path: index-traversal`: a term ranking over concept titles and descriptions (which is what every index entry is made of, verbatim), the bodies at lower weight, and one hop along the authored links, so a concept the bundle links to a hit surfaces behind it. Accents are folded, so `informaticos` matches `Informáticos`.
- a matrix that exists but **does not cover every concept** → both, as `path: embeddings+index-traversal`. The covered concepts are ranked by kNN, the rest by traversal, and the two lists are alternated by rank rather than sorted together, because a cosine similarity and a term-overlap score are different quantities. `note` says how many were ranked without a vector and that their scores are not comparable with the others'.

**Read `note` whenever it is not empty.** Vectors come from `okragged index`, a separate command, so a concept written since the last one has no row in the matrix — and kNN cannot return a candidate that is not in it. That case used to match neither fallback, so `search` answered from the vectors it had, with an empty note and no sign that anything had been skipped: a query carrying a concept's exact title came back with eight other concepts. An error would have been better; the fix is that a concept without a vector is ranked by the other path instead of being left out, and that the note is empty only when the ranking saw everything.

The result shape is identical across all three, and the response names which path answered — plus, where the ranking was incomplete or the embedding path could not run, why. Retrieval strategy is deliberately out of scope ([`DESIGN.md` §7](DESIGN.md)); this ranking is meant to be simple enough to explain in a sentence and test without a model.

`add_source` **records, and authors the concept only if the instance was launched to spend.** It writes the prose into `sources/` under a path it generates; on a `--write` instance it stops there and the next `okragged build` authors from it, and on a `--write --spend` instance it goes on to ingest that one path, refresh the vectors over it, and return the concept ids. The refresh is not a separate decision: a concept `search` cannot rank is not worth what the synthesis cost, and `okragged index` is a command nothing on this surface can trigger. It costs one embedding, since a vector whose body hash is unchanged is reused. `indexed: false` with an `errors` entry means the concept is published and readable and only its vector is missing — `[vectors]`' key can be absent while `[synth]`'s resolves — so `search` finds it by traversal until `okragged index` runs. The two are registered as separate tools carrying separate descriptions, because the description is the only channel every client shows a model: one that promised a concept where none is coming, or told a caller to run a build that already ran, is worse than saying nothing. Neither can sweep — a partial run over one path suppresses the vanished-source check (§6.1), and recording starts no run at all.

**Recording takes no lock; synthesizing does.** The path is generated per assertion and identical bytes reuse it, so a recording call has nothing to collide with and taking the lock would make it fail for the duration of somebody's rebuild. Two concurrent recordings that land on the same generated name — same title-derived slug, same day — resolve correctly rather than one silently overwriting the other's prose: the file is claimed with an atomic exclusive create, the same technique the project lock itself uses, so the loser sees the name taken and either matches it (identical text) or takes the next suffix (different text). Ingesting goes through the pipeline, so it takes the project lock like any other writer — and a build already holding it comes back as a message naming what holds it, not as a traceback. Same for a missing `[synth]` key. In both cases the prose is already recorded and the answer says so: raising there would tell you your assertion was lost while it sat in `sources/` waiting for the build that will pick it up. `recorded: false` means that prose was already on file; `synthesized: false` with an `errors` entry means the file landed and the concept did not.

**The contract itself is served over the protocol**, because a consumer in another checkout cannot open `agent/AGENTS.md`:

| Resource | What it returns |
|---|---|
| `okragged://contract` | `AGENTS.md`, rendered by the same function that writes the file |
| `okragged://index` | the bundle's root `index.md` |

Above them, the handshake `instructions` say what the bundle is and to fetch the contract — short, self-contained, and naming no path. Nothing the server says over the wire may name a path relative to the producer's root, and `tests/test_agent.py` enforces that with a check pointed at the text it replaced, so it cannot quietly stop biting.

The server needs the MCP SDK, an optional extra — `pip install -e ".[mcp]"`. Everything else in the contract is generated without it.

### Hosting over http — `okragged mcp --transport http`

A second way to run the server, beside stdio: over a port, for many remote consumers, rather than for one local client `.mcp.json` spawns. It exists because credential-free reads — the whole point of `search` and `read_concept` working with no key at all — are a feature over stdio and an exposure the moment the same server is reachable over a port: anyone who can reach it reads the whole corpus. So this transport authenticates every request as an **OAuth 2.1 resource server**. okragged never issues tokens; it verifies bearer tokens an external authorization server issued, against that issuer's published JWKS. Configure the issuer in `okragged.toml`, not `.env` — none of these three is a secret, only the issuer's public identity and this server's own resource URL:

```toml
[mcp]
issuer_url = "https://issuer.example.com"
resource_server_url = "https://mcp.example.com"
jwks_url = "https://issuer.example.com/.well-known/jwks.json"
required_scopes = []   # optional; a token must carry every scope listed

# only needed for --write --spend together — a synthesizing add_source
spend_scopes = ["okragged:spend"]   # a token must carry every scope listed here too
spend_rate_limit_per_hour = 20      # per identity (the token's `sub`, or its `client_id`)
```

```powershell
okragged mcp --transport http --host 0.0.0.0 --port 8001   # 8001, not serve's 8000
okragged mcp --transport http --write --spend               # add_source too, gated below
```

All three URLs are required — missing any of them fails fast, before the server binds a port, naming which `[mcp]` key is absent. A request with no bearer token, or one that fails signature, issuer, audience, expiry, or scope verification, gets a `401` with a `WWW-Authenticate` header pointing at this server's `.well-known/oauth-protected-resource` metadata; no check is skippable. A validly authenticated request reaches exactly the tools a stdio instance with the same `--write`/`--spend` flags offers.

**`--write` alone, and `--spend` alone, are unconditional** — plain `--write` only records a source, which is concurrency-safe since [§13 M8 criterion 3](DESIGN.md#13-milestones); `--spend` alone only lets `search` embed a query on the host's key, unchanged since M6. Neither needs anything beyond the three URLs above.

**`--write --spend` together — the synthesizing `add_source` — additionally needs `spend_scopes` and `spend_rate_limit_per_hour` set, or the server refuses to start naming whichever is missing** ([§13 M8 criterion 4](DESIGN.md#13-milestones)). This is where the token's identity — its `sub`, a claim the caller cannot forge, since an external issuer signed it — finally does something: a call from a token missing every scope in `spend_scopes` is refused before anything is written, and a call from an identity that has already spent `spend_rate_limit_per_hour` times in the last hour is refused the same way. The limit is per identity, not per call, because cost follows the configured model and the number of sections a source becomes, and it lives in server memory for the life of the process — a remote caller has no filesystem access to the host to delete a counter the way M5/M6's single-party caller could, so nothing is lost by not persisting it. The stdio path is completely untouched: `.mcp.json` still spawns the unauthenticated, local-process-only server it always has, where `--write --spend` still means only "the operator who launched this instance is paying" (above).

**A fifth key, `verify_scopes`, gates a fifth tool: `verify_concept`** ([§13 M8 criterion 5](DESIGN.md#13-milestones)):

```toml
[mcp]
# ...the three URLs above, plus:
verify_scopes = ["okragged:verify"]   # a token must carry every scope listed here too
```

`verify_concept(concept_id: str)` appends one `{by: "human:<identity>", at: <now>}` entry to that concept's `verified` list — never replacing it, and never touching `status`, which stays a separate, human-owned file edit. `<identity>` is the caller's own `sub`/`client_id`, taken off the verified bearer token, never a string the call supplies — the same binding `spend_scopes` gives spend, minus a rate limit, because verifying spends nobody's credential and there is nothing to bound per hour. Unlike `spend_scopes`, an unset `verify_scopes` is not a misconfiguration: there is no CLI flag of its own, so a `--write --transport http` deployment that leaves it empty is simply one that does not offer verification, and the tool is absent from that instance's tool list rather than present and refusing. A missing scope, or no authenticated identity at all, is refused before anything is written; once an entry lands, the concept is protected from a future `okragged build` overwriting it, the same way a hand-edited `verified` block already is.

### What gets written into frontmatter

```yaml
---
type: Policy                        # required by OKF; from the okragged.toml vocabulary
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
  by: okragged/0.1.0
  at: '2026-08-04T04:29:07Z'
status: draft
x_okragged_source_hash: 92933ee5... # producer extension; which source revision this is
---
```

**`verified` is never written because content looks correct.** That field records a confirmation event that actually happened, so writing it on that basis would forge the human-reviewed trust tier. A human promotes a concept by editing the file — or, on a hosted instance configured with `[mcp].verify_scopes`, by calling `verify_concept` over an authenticated session, which appends an entry attributed to their own verified identity rather than to text they typed (see [Hosting over http](#hosting-over-http--okragged-mcp---transport-http) above). Concepts are emitted as `status: draft` and stay there until a human says otherwise; `verify_concept` never changes that either.

New `type` values proposed by the model are **reported, never silently accepted** — they appear in the build summary so you can decide whether to add them to `okragged.toml`.

### Validation

Two layers, because neither alone is enough:

1. **Built-in checks** — always run, no dependencies. The three OKF conformance rules (frontmatter parses; `type` non-empty; reserved files well-formed) as errors `E1`/`E2`/`E3`, plus warnings `W1` (missing `title`/`description`), `W2` (broken cross-link), `W3` (no `generated.by`), `W4` (no `index.md` in a directory), `W5` (log dates not ISO or not newest-first).
2. **okflint** — the normative gate. Runs `okflint validate --manifest okf-base.yaml <bundle>`; exit 0 required.

Broken links are **warnings, not errors** — the spec explicitly permits them, since a link can point at knowledge not yet written.

`okf-base.yaml` is generated because okflint's `validate` always resolves a manifest and exits 2 without one. It is deliberately minimal — no `profile` block (that would reject newly proposed types, and required-field policy is the bundle owner's call) and no `okf_version` key (okflint 0.3.1 recognises only `0.1` and warns otherwise; the bundle-root `index.md` is where `0.2` is declared). If okflint is missing or the manifest is absent, validation reports *why* it was skipped rather than silently degrading.

### One writer, and a commit per source

Three things can write this directory — the CLI, the Python API, and a synthesizing `add_source` over MCP — so a write takes `.okragged/lock.json` for its duration and a second one is refused with a message naming what holds it:

```
another okragged run holds this project: ingest, pid 26104 on DESKTOP-A, since 2026-08-17T18:02:11Z.
Reads are unaffected - `search`, `read_concept`, `lint` and `check` do not take the lock.
```

**A stale lock never needs a human.** The record says who holds it, so a dead holder is told from a live one — the pid is probed on this host, and a lock from another machine goes stale on age — and the next run reclaims it and says so. A crashed build that required a file to be deleted by hand would be a worse failure than the one being prevented.

**Read paths never take it.** `search`, `read_concept`, `lint`, `check` and `serve` answer while a write is in flight, and so does `build --dry-run`. A consumer holding a directory of markdown files needs permission from nobody.

The emit is **staged**, and the claim is *recoverable*, not atomic — no filesystem call publishes several files at once on Windows, so the design says so instead of pretending. **The unit that commits is one source**: its concepts go into `.okragged/staging/bundle/` beside the ledger that belongs with them, and one fenced commit places those files, removes the stale output the source's rename left behind, and moves the ledger into place.

| What is on disk afterwards | What the next run does |
|---|---|
| nothing extra | nothing — every commit finished |
| `pending-commit.json` (with or without `staging/`) | **finishes** that commit; the record is only written once the source's output is complete |
| `staging/` and no record | **rolls back** that one source — the sources committed before it stand, its staged files are reported and cleared, and only it is redone |

Either way the next build prints what it found, because a repair that happened silently would hide the fact that an earlier run died.

**Why the source and not the run.** **Concept ids come from model-written titles**, so a rebuild renames that source's whole tree as a matter of course — and the dangerous window is *inside* one source's emit, between writing the new names and deleting the old ones. Half of that is not a partially updated bundle, it is a mix of two namings, and stale-output cleanup reconciles against a complete emit or not at all. Across sources there is no such window: one source's new concepts beside another's untouched old ones is a complete, valid bundle. Fencing the whole run therefore protected nothing extra and cost everything an interrupted run had paid for — a forty-source build interrupted at source thirty-nine re-paid thirty-nine sources' worth of model calls.

**The ledger never outruns the bundle**, per source rather than per run: a source recorded in the ledger is a source whose concepts are published. That is the half that cannot be relaxed — a ledger that ran ahead would make the content-hash skip believe work was done that was not, and the corpus would lose sources silently. The other direction only costs a re-synthesis, and the extraction cache survives it.

**What an interrupt leaves, by phase.** Between sources: every finished source published and recorded, the one in flight staged and cleared by the next run. Mid-commit: the pending record, which the next run finishes. Before link-weave: published concepts with no cross-links — the state a *failed* weave already produces, so the source is not marked incomplete and is not retried for the links it lacks. Mid-weave: every concept either woven and re-recorded or unwoven and recorded, never woven against a stale hash, because each rewrite commits with the hash the ledger notes for it. And the indexes, `log.md` and `okf-base.yaml` are derived from the bundle rather than accumulated, so a run that dies before writing them leaves them stale and any later run regenerates them.

The revision model that *many remote* writers need — an expected revision on every mutation — is deliberately not here; that is the hosted server's problem ([`DESIGN.md` §13](DESIGN.md#13-milestones) M6).

---

## Driving it from Python

Everything the CLI does is a function call, and `okragged` exposes the same set under one import. Ten functions, each taking a project directory or a `Config`:

```python
import okragged

cfg = okragged.configure("~/corpus", synth={"provider": "gemini"})
summary = okragged.build(cfg, progress=lambda e: print(e.stage, e.unit))
print(okragged.check(cfg).ok, [h.concept_id for h in okragged.search(cfg, "revenue").hits])
```

| | | Needs a credential |
|---|---|---|
| `configure(project, **sections)` | a `Config`, reading the project's `okragged.toml` and applying section overrides in memory | no |
| `build(project, *, dry_run, only, contract, progress)` | the ingest pipeline → `pipeline.IngestSummary` | `[synth]`, unless `dry_run` |
| `index(project)` | embed → project → `graph.json`, into `.okragged/` only | `[vectors]` |
| `check(project)` | validate an existing bundle → `okf.Report` | no |
| `search(project, query, k=8)` | pointers, never bodies — the same ranking as the MCP tool | only for the embedding path |
| `read_concept(project, concept_id)` | the raw file, frontmatter included | no |
| `lint(project)` | conformance, ghosts, stale, orphans → `LintReport` | no |
| `add_source(project, text, title=None, synthesize=False)` | record prose in `sources/`; the concept waits for `build` unless `synthesize` | `[synth]` to synthesize |
| `write_contract(project)` | regenerate `agent/AGENTS.md` | no |
| `register(project, write=False, spend=False)` | register `okragged mcp` in `.mcp.json`, keeping other servers; the switches go into the entry's args | no |

The wrappers return what the underlying functions return. There is no `Bundle` object, deliberately: a bundle is a directory ([`DESIGN.md` §2](DESIGN.md#2-the-three-layer-model)), and handing back an object that stands for one would answer a question the design has not decided.

**One thing differs from the CLI, and one thing nothing does:**

- **No `.env` is read.** `okragged` the command loads it because a human typed the command; a function call does not go looking through your filesystem for credentials. Put the key in the environment first (`os.environ`, `python-dotenv`, your process manager — your choice).
- **Nothing registers an MCP server for you.** `build()` regenerates `agent/AGENTS.md` exactly as `okragged build` does — one derived file beside the bundle you asked for, and `contract=False` skips it. Neither one writes `.mcp.json` — `register()` is the only call that touches it, for the reason given above.

`progress` is called as the run moves, with a frozen `Progress(stage, unit, index, total, detail)`: `stage` is one of `discover`, `extract`, `plan`, `synthesize`, `weave`, `source`, `finalize`, `validate`; `unit` is the source uri, or the concept id while weaving; `source` fires once per source however it finished. A reporter that raises is swallowed rather than allowed to abort a run that has already paid for model calls.

`import okragged` costs nothing and prints nothing — every import inside it is deferred, because `markitdown` announces a missing ffmpeg on import and a library that writes to your stderr to be imported is broken.

### Example: serving it from Python

`create_app` isn't in the curated surface above — it needs the `api` extra, and it doesn't return a summary; it blocks for as long as the server runs, the same as `okragged serve` does. Import it from `okragged.api` and drive `uvicorn` yourself:

```python
import uvicorn
from okragged import configure
from okragged.api import create_app

cfg = configure("~/corpus")
app = create_app(cfg)  # raises RuntimeError without `pip install -e ".[api]"`
uvicorn.run(app, host="127.0.0.1", port=8000)
```

There's no read/write switch here — the HTTP API is unconditionally read-only, the same four endpoints `okragged serve` exposes ([The graph view](#the-graph-view--okragged-serve)). The read/write/spend distinction belongs to `okragged mcp` (`--write`, `--spend`), not to `serve`.

---

## Important files

### Source

| File | Purpose |
|---|---|
| `src/okragged/__init__.py` | The [public Python API](#driving-it-from-python) and nothing else: ten functions that resolve `str \| Path \| Config` and delegate to the function the CLI calls, plus `Config` and `Progress`. Every import in it is deferred, so importing the package is silent and cheap. |
| `src/okragged/cli.py` | Typer CLI: `build`, `index`, `serve`, `check`, `agent`, `mcp`, `extract`, `doctor`. Formatting and exit codes. |
| `src/okragged/pipeline.py` | Orchestrates the stages; assembles `Concept` objects from synthesis results. Owns `Progress`, the progress callback's event, the lock the run is wrapped in, where each source's commit falls, and the `contract` flag that decides whether layer 3 is regenerated after them. |
| `src/okragged/ledger.py` | The idempotency ledger, `.okragged/manifest.json`: what each source hashed to, which concepts it produced, and what okragged last wrote into each. Sidecar state — losing it costs a re-ingest, never correctness, so every read path degrades to "no record" rather than raising. Not `okf/manifest.py`, which is the *okflint* manifest. |
| `src/okragged/lock.py` | One writer per project, `.okragged/lock.json`: exclusive creation, a record naming its holder, and the liveness test that lets a dead holder's lock be reclaimed without a human. Never taken by a read path. |
| `src/okragged/staging.py` | The per-source commit and its pending-commit record. Owns the placing and its replay, so a crash leaves one source the next run can finish or roll back rather than a mix of two namings. |
| `src/okragged/config.py` | Loads `okragged.toml` into pydantic models. Owns the `bundle/`, `sources/`, `.okragged/` path resolution. `bundle_root` is the published bundle and nothing else — where a staged run writes is `staging.Area`'s job. |
| `src/okragged/sources/base.py` | The `Source` protocol and `SourceItem`. Defines the `document` vs `asset` family split that phase-2 connectors plug into. |
| `src/okragged/sources/local.py` | `LocalFileSource` — the only implemented source. Globbing and hashing. |
| `src/okragged/extract/__init__.py` | Bytes → text, with the content-hash cache. markitdown plus passthrough. |
| `src/okragged/synth/prompt.py` | **The synthesis, planning and link-weave system prompts.** Each must stay byte-identical across its pass or prompt caching stops working; the only things that vary are the type vocabulary and the concept index, both snapshotted once per run. Tune concept quality here. |
| `src/okragged/synth/segment.py` | The planning pass and `cut` — marker matching, document-order sorting, short-section folding. `cut` is pure, so segmentation is testable without a model. |
| `src/okragged/synth/weave.py` | The link-weave pass and `prune_links` — unwrapping link targets that are not concepts. `prune_links` is pure, so the repair half is testable without a model. Re-anchors a footnote definition the woven body left mid-line, because this pass rewrites the body after the pass that appended it. |
| `src/okragged/synth/__init__.py` | Provider dispatch plus everything shared: prompt assembly, JSON parsing, required-field checks, footnote repair — appending a definition never written, and anchoring one left mid-line, where it renders as literal text. |
| `src/okragged/synth/base.py` | The concept and outline JSON schemas, the §4.3 word bounds, `SynthResult`, `SectionContext`, and the error types (`Refused`, `ProviderHTTPError`, `MissingCredential`, `SynthError`). |
| `src/okragged/synth/providers/claude.py` | Anthropic: `output_config.format`, cached system block, `stop_reason` handling. |
| `src/okragged/synth/providers/gemini.py` | Gemini: `response_json_schema`, `system_instruction`, `finish_reason` / `block_reason` handling. |
| `src/okragged/okf/concept.py` | The OKF data model. `render` / `parse` round-trip, unknown-key preservation, `slugify`. |
| `src/okragged/okf/inventory.py` | The format layer's read side: what is already in a bundle, as `(id, title, description, status)`. Feeds the prompt prefix, link-weave and the indexes. |
| `src/okragged/okf/writer.py` | Atomic concept writes, the per-directory `index.md` pass, `log.md` date-section insertion, deprecation and stale-concept removal. |
| `src/okragged/okf/validate.py` | Conformance checks and the okflint subprocess. |
| `src/okragged/okf/manifest.py` | Generates `okf-base.yaml`. Documents why each key is present or omitted. |
| `src/okragged/vectors/__init__.py` | `index(config)`: embed → project → graph, and the only place the three stages are wired together. |
| `src/okragged/vectors/embed.py` | One vector per concept: chunking, mean-pooling, hash-based reuse, per-concept failure isolation. |
| `src/okragged/vectors/store.py` | `embeddings.npy` + `embeddings_meta.json`. Sidecar state, so a read degrades to "nothing stored"; a provider or model change discards the matrix rather than mixing vector spaces. |
| `src/okragged/vectors/project.py` | UMAP with a fixed seed, PCA fallback in plain NumPy. Notes what Risk 5 would actually take to fix. |
| `src/okragged/vectors/knn.py` | Brute-force cosine kNN over the normalized matrix. Twenty lines, and enough until ~100k concepts. |
| `src/okragged/vectors/graph.py` | `graph.json`: the ghost-edge rule, derived trust tiers, node degree, stats. |
| `src/okragged/vectors/providers/gemini.py` | `gemini-embedding-001`. Same shape as a `synth` provider: `NAME`, `DEFAULT_MODEL`, `ENV_VARS`, `credential_present()`, `embed()`. |
| `src/okragged/vectors/providers/fastembed.py` | Local `bge-small-en-v1.5`, behind an optional extra. Registered but not chosen — see `DESIGN.md` §12 Risk 4. |
| `src/okragged/agent/query.py` | `search` and `read_concept`: the embedding path, the credential-free traversal ranking, the merge that ranks a concept the stored vectors do not cover instead of dropping it, and the id resolution that keeps a read inside the bundle. |
| `src/okragged/agent/write.py` | `add_source`: the generated path, the recording mode, and the synthesizing one that ingests that path and refreshes the vectors. Nothing after the file is written may raise — every failure is a field in the result. |
| `src/okragged/agent/lint.py` | The lint report: conformance from `okf/validate.py`, ghost edges read out of `graph.json`, stale concepts, orphans. |
| `src/okragged/agent/contract.py` | `write_contract` generates `agent/AGENTS.md` and renders what `okragged://contract` serves; `register` writes the `.mcp.json` entry and is called only when asked. No timestamp, so an unchanged bundle regenerates byte-identically. Documents why the two are separate. |
| `src/okragged/agent/server.py` | The four MCP tools and the two resources, over those functions. Imports the SDK lazily, and writes nothing to stdout — the protocol lives there. |
| `src/okragged/api/app.py` | The four read-only HTTP endpoints, over the same functions the MCP tools use. Imports FastAPI lazily; mounts the built view from inside the package - never from the user's project root - when it exists. |
| `web/src/encoding.ts` | §8's visual encoding in one place — type colours, degree→size, trust→border — so the legend and the renderer cannot drift. |
| `web/src/Graph.tsx` | The sigma renderer. Filtering happens in reducers rather than by rebuilding the graph, so dragging τ costs a repaint. |

### Config and output

| Path | Committed? | What it is |
|---|---|---|
| `okragged.toml` | yes | Configuration. See below. |
| `.env.example` | yes | Template for the API key. Copy to `.env`. |
| `.env` | **no** (gitignored) | Your API key. Read by CLI entry points that load credentials, including `build`, `doctor`, and `mcp --spend`. |
| `sources/` | **no** (gitignored) | Layer 1 — raw input, yours to supply. **Immutable**; okragged reads, never writes. Not committed: a real corpus is your own material and tends to carry third-party names that do not belong in a permanent history. |
| `bundle/` | **no** (gitignored) | Layer 2 — the OKF product. The deliverable, portable with no okragged dependency — but concept ids derive from model-written titles, so every rebuild renames the whole tree and the diff is unreadable. Commit an example bundle under `examples/` instead, refreshed on purpose. |
| `okf-base.yaml` | **no** (gitignored) | Generated okflint manifest, rewritten with the bundle. |
| `agent/` | **no** (gitignored) | Layer 3 — the generated contract. Not committed for the same reason as `bundle/`: `AGENTS.md` is written from the corpus and carries its names. `okragged agent` rewrites it. |
| `.mcp.json` | **your call** (not gitignored) | The registration entry: `okragged mcp`, the bare console script when `PATH` resolves it and the one beside the running interpreter when it does not. Other servers already in the file are preserved. Written only by `okragged agent --register` — it is your machine's MCP config rather than okragged output, so committing it is your decision. |
| `.okragged/` | **no** (gitignored) | Sidecar: the ledger (`manifest.json`), the extraction cache, replacements for concepts a human owns (`proposed/`), the write lock and the source in flight (`lock.json`, `staging/`, `pending-commit.json`), and the vectors — `embeddings.npy`, `embeddings_meta.json`, `layout.json`, `graph.json`. Entirely derived and rebuildable — deleting it costs a full re-ingest and a re-embed, nothing more. |
| `web/` | yes, minus `node_modules/` | The graph view's source. Built deliberately with `npm run build`, which writes into `src/okragged/api/static/` (gitignored) so one build serves this checkout and an installed user alike. |
| `DESIGN.md` | yes | Architecture, data model, risks, milestones. |

The separation matters: **nothing derived ever goes inside `bundle/`.** A bundle must stay something you can zip up, hand to someone who has never heard of okragged, and read with `cat`. That portability is the whole point of OKF, and an embeddings file or an `embedding:` frontmatter key would destroy it.

### `okragged.toml`

```toml
[bundle]
root = "bundle"            # output directory
okf_version = "0.2"        # written into the bundle-root index.md
actor = "okragged/0.1.0"   # written as generated.by; defaults to the installed version
# title = "Derecho Informático"  # heading of the root index.md; omit for the
                           # project directory name, which names the tool

[sources.local]
family = "document"        # reserved; only "document" is implemented
path = "sources"
include = ["**/*.md", "**/*.pdf", "**/*.docx", "**/*.xlsx", "**/*.pptx", "**/*.html"]
exclude = ["**/~$*", "**/.DS_Store"]

[synth]
provider = "gemini"        # gemini | anthropic
# model = "gemini-2.5-pro" # omit for the provider default; `okragged doctor` lists what your key can reach
effort = "high"            # anthropic only: low | medium | high | xhigh | max
max_tokens = 16000

[types]
allowed = ["Document", "Concept", "Entity", "Process", "Decision", "Reference"]
allow_new = true           # new types are reported in the summary, never silent

[vectors]                  # okragged index only; build never reads these
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
.\.venv\Scripts\python.exe -m pytest tests -q
```

418 tests. No credentials and no network needed — the end-to-end test stubs the model and asserts the emitted bundle is conformant, so "one file in, one conformant concept out" is covered offline. The embedding provider is stubbed the same way, in a three-dimensional toy space, so what two concepts are "similar" is something the test states rather than something a model decides.

| File | Covers |
|---|---|
| `tests/test_okf.py` | render/parse round-trip, extension-key preservation, `E1`/`E2`/`E3`, link resolution (absolute and relative), log ordering and date merging |
| `tests/test_reingest.py` | the ledger: a second build over unchanged sources making zero model calls, what invalidates a skip (edited source, deleted bundle, corrupt or future-version ledger, a section that failed last run), deprecation of vanished sources, stale-output removal after a rename, id reservation across a partial rebuild, and human-edit/`verified` protection |
| `tests/test_pipeline.py` | end-to-end with a stubbed model, dry run, extraction-failure isolation, non-2xx isolation, an interrupted run leaving a bundle of the sources that committed, id collisions, new-type reporting, footnote repair, and the segmented path: parent plus children, frontmatter-less child index, parents-only root index, one failed section vs all of them |
| `tests/test_segment.py` | `cut` against text whose lines break mid-sentence, unfound and out-of-order markers, short-section folding, the split threshold, and the outline call's required fields |
| `tests/test_index.py` | labels and descriptions taken from the target's frontmatter (accents intact), the bundle title from `[bundle].title`, the root marking a concept that owns a subtree, an index for every directory including one no concept owns, subtree counts, the deprecation marker, and indexes read off disk rather than accumulated |
| `tests/test_weave.py` | `prune_links` against all three repair modes plus self-links, relative links, URLs and footnotes; the prefix/user-message split that keeps caching working; the pass itself linking two concepts, telling each child its parent, recording the post-weave hash, surviving a failure, and skipping a concept a human owns |
| `tests/test_manifest.py` | manifest keys okflint requires, skip-with-reason behaviour, and cross-checking that okflint agrees with the built-in checks |
| `tests/test_providers.py` | provider registry and interface, dispatch, schema/required-field enforcement, Gemini credential resolution and decline mapping, non-2xx and transport-failure mapping for both providers, fail-fast pre-flight |
| `tests/test_vectors.py` | the embedding provider registry, hash-based reuse and per-concept failure isolation, mean-pooling past the window with no chunk vector stored, L2 normalization, a model change re-embedding instead of mixing widths, the PCA fallback and its determinism, the ghost rule (τ, top-k, a link in either direction), derived trust tiers, graph stats, and that `index` writes nothing into `bundle/` |
| `tests/test_agent.py` | each tool's contract: `search` returning pointers and not bodies, the credential-free traversal path and its ranking (title over body, a linked neighbour over an unrelated mention, folded accents), the embedding path, the fallback when a query cannot be embedded, and the merge that keeps a concept written since the last `okragged index` from being dropped from the ranking in silence, `read_concept` refusing a path out of the bundle, the lint report's orphans/stale/ghosts, the generated `AGENTS.md` content, the split between the contract and registration — that no build of either kind writes `.mcp.json`, that `okragged agent` offers `--register` until it has been used, and that a merge keeps other servers, names a console script rather than an interpreter, and cannot truncate a config it fails to rewrite — `okragged mcp` taking its target from `--project` or the environment, the two resources and the contract served over the wire matching the file on disk byte for byte, a partial `ingest` not deprecating what it did not look at, a source another writer blocked being reported with `synthesized: false` rather than raising over prose already in the corpus, a spending write leaving its concept findable by `search` and saying `indexed: false` with the reason when it cannot, the contract describing the instance modes rather than asserting one, and that none of it writes into `bundle/`, plus `verify_concept` appending an entry to an empty or a non-empty `verified` list without ever touching `status`, refusing an unknown `concept_id`, and two concurrent verifications of the same concept losing neither entry under the project lock. Plus the M5 invariant — nothing the server declares may name a path relative to the producer's root — with a meta-test pointing the check at the M4 text it replaced. The server tests skip without the `mcp` extra. |
| `tests/test_library.py` | the public API: that importing the package prints nothing and pulls in neither the extraction path nor a provider, `configure` overriding a section in memory, a library `build` regenerating the contract exactly as `okragged build` does while neither registers an MCP server, `register()` being the only call that writes one, no `.env` read from the library path, the progress callback's stages and units, an event that cannot be mutated, a reporter that raises not failing the run, and a path and a `Config` being interchangeable |
| `tests/test_env.py` | `.env` loading with and without a BOM, and every `doctor` outcome |
| `tests/test_writers.py` | the lock and the per-source commit: a second writer refused with the holder named, a lock left by a dead process reclaimed with no human in the loop, an unreadable lock file not wedging the tool, a foreign host judged on age, a reclaimed lock not deleted by the run that lost it, every read path answering while a write holds the lock, a source published by its own commit rather than by the run, the ledger never describing a source whose concepts are not published, a second run paying only for what the interrupted one never committed, a commit interrupted while placing a concept finished by the next run, an unreadable record still publishing what was staged, a commit interrupted between placing the new naming and removing the old one, a vanished source deprecated and forgotten in one commit, and the two interrupts around the weave — before it, concepts published without cross-links and not marked incomplete; during it, no concept left woven against a stale hash |
| `tests/test_encoding.py` | the [encoding policy](#encoding-policy), enforced by AST scan (including the child-process `env` rule), plus BOM tolerance, the piped-child crash reproduced, and LF-only output end to end |
| `tests/test_cli_view.py` | `okragged build-view` / `okragged serve --all`: staleness detection against a fake `web/` + built-output layout, and the `npm` subprocess wrapper skipped gracefully when `npm` is missing or `web/` does not exist - `subprocess.run` itself is monkeypatched, so a cold `node_modules` never needs network |
| `tests/test_packaging.py` | the packaging defect class fixed in `4cac126` - a real `python -m build --no-isolation` (offline, since `build` and `hatchling` are `dev` dependencies) inspected directly for `okragged/sources/` and the built `api/static/index.html` in both the wheel and the sdist |
| `tests/test_agent_auth.py` | `JWKSTokenVerifier` against a locally generated test keypair - a valid token accepted, a wrong signature/issuer/audience/expiry/scope each rejected, no network call; the wired-up `MCPServer`'s `streamable_http_app()` driven in-process with Starlette's `TestClient` - an unauthenticated request refused with a `WWW-Authenticate` header naming the protected-resource metadata, an authenticated one past the auth gate, and the route table carrying no bundle or static file mount; criterion 4's `authorize_spend`/`SpendRateLimiter` offline against a directly-constructed `AccessToken` - no scope, an exhausted limiter, and a different identity left unaffected, all with an injectable clock - and the write+spend tool driven with `mcp.Client`: a refused spend writing nothing and never reaching `add_source`, an authorized one recording and synthesizing as usual, and the stdio path never calling the gate at all; plus criterion 5's `authorize_verify` offline the same way (no token, no scope, a matching scope named), and `verify_concept` driven with `mcp.Client`: absent with `write` off, absent with `verify_scopes` empty, absent under stdio regardless of flags, a refused call leaving the concept file untouched, and an authorized one appending the entry with `status` left alone |
| `tests/test_cli_mcp_http.py` | `okragged mcp --transport http`'s own validation, `agent.server.main` monkeypatched so nothing binds a socket: an unknown `--transport` value, `--write`/`--spend` each allowed alone with no extra config, `--write --spend` together refused with no `spend_scopes`/`spend_rate_limit_per_hour` configured and allowed once both are, `[mcp]` config missing naming which key (exercised for real, since these paths fail before touching the MCP SDK), and both the http and the unchanged stdio call reaching `main` with the right arguments |

Nothing skips on a normal install. okflint is resolved beside the running interpreter before `PATH` is consulted, so a venv install is found whether or not a shell activated it — which is also why `lint` over MCP reports real conformance rather than "okflint not installed".

---

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

**`tests/test_encoding.py` enforces this statically** — it walks the AST of everything under `src/okragged/` and fails the build if any read, write, or subprocess call deviates. It also contains a meta-test pointing the check at a deliberately bad file, because a guard that always passes guards nothing.

The five bugs, for reference:

1. **`.env`** — python-dotenv reads UTF-8 by default, so a BOM produced a key literally named `﻿GEMINI_API_KEY`. Silent: `.env` reported "found" while the credential reported "missing".
2. **Passthrough extraction** — a BOM rode into the synthesis prompt as a leading invisible character.
3. **okflint subprocess** — `text=True` decoded with cp1252, okflint emits characters outside it, the decode raised inside the reader thread and `stdout` came back as `None`.
4. **Redirected stdout** — `okragged extract x.pdf > out.txt` raised `UnicodeEncodeError` on any character outside cp1252. Deceptive because `acción` and `€` *are* in cp1252, so it only broke on `≥`, `—`, `≈` and curly quotes — exactly what a model writes into titles and descriptions.

5. **okflint's own stdout** — fixing #3 only fixed *our* decode. okflint's stdout is a pipe, so its Python chose cp1252 and raised `UnicodeEncodeError` inside its own `print()` while reporting a finding containing `—`. It died mid-report, so its exit code was non-zero and `okragged build` declared a perfectly conformant bundle "NOT conformant: 0 error(s)". The fix is `PYTHONIOENCODING=utf-8` in the child's `env`.

That fourth one was masked in development: Claude Code sets `PYTHONIOENCODING=utf-8:surrogateescape`, so it could not reproduce until the variable was cleared. If you are testing terminal behaviour, check what your environment sets first.

---

## Roadmap and open work

Full roadmap with exit criteria in [`DESIGN.md` §13](DESIGN.md#13-milestones).

### M2 — done

All four items in [`DESIGN.md` §13](DESIGN.md#13-milestones):

- **Segmentation** (§4.3). A source over ~1200 words is planned into sections and each becomes its own concept under a parent. Measured on a 3-PDF corpus of 86k characters, retention went from 9% of the source to 48%.
- **Incremental re-ingest** (§5, §6.1). A `build` over an untouched corpus makes zero model calls; segmentation had multiplied the cost of not having this, since one source is N+1 calls rather than 1. Also deprecation of vanished sources, stale-output cleanup after a rename, and human-edit protection.
- **Link weaving** (§6.5). The bundle is a graph rather than a set of small trees. Note that links point forward from concepts written this run to concepts that already existed, and not back — a skipped source's concepts are not rewritten, because that would spend a model call producing a file nobody asked for. The missing direction is what M3's ghost edges are for.
- **Per-directory `index.md`** (§6.6), read off the bundle rather than accumulated during the run.

### M3 — done

`okragged index` writes `embeddings.npy`, `embeddings_meta.json`, `layout.json` and `graph.json`, all into `.okragged/`; `okragged serve` exposes four read-only endpoints over them and mounts the sigma.js view. The embedding provider is pluggable and **deliberately undecided** ([`DESIGN.md` §12](DESIGN.md#12-risks-and-open-questions) Risk 4): `gemini` and `fastembed` are both registered, and the choice is config.

Run against the 3-PDF Spanish corpus: 18 concepts embedded with `gemini-embedding-001` at dim 768, projected with UMAP, 39 authored links, 76 ghost edges, 0 broken links, and `search` answered by the **embedding** path rather than by traversal for the first time. What the graph then said about τ is above, and it is the milestone's actual result: **the default threshold does not discriminate on a single-topic corpus.**

### M4 — done

`agent/AGENTS.md`, regenerated at the end of every build, plus the stdio MCP server and on-request `.mcp.json` registration. Verified against the same 3-PDF corpus by spawning the server over stdio exactly as `.mcp.json` does, with no embedding credential: `search` answered by traversal and named the right concept first, `read_concept` returned it whole, `lint` reported 18 concepts and okflint exit 0, and it said it had no ghost edges *because `okragged index` had not run* rather than because there were none.

### M8 — done

`okragged mcp --transport http`: a second way to run the server, over a port, as an OAuth 2.1 resource server, beside the untouched stdio path. See [Hosting over http](#hosting-over-http--okragged-mcp---transport-http) for the config and the tool-gating rules. Five criteria:

1. **Authenticated reads.** okragged never issues tokens; it verifies bearer tokens an external issuer signed, against that issuer's published JWKS — no check (signature, issuer, audience, expiry, scope) is skippable.
2. **The bundle is reachable only through tools, never as files.** Holds by construction — the MCP SDK's `streamable_http_app()` never mounted a static route — and is locked in by a test reading the wired-up route table directly.
3. **Concurrency is a guarantee, not an assumption.** A real check-then-act race in `add_source`'s new-file path (two concurrent calls landing on the same title-derived slug could silently overwrite one writer's prose) is closed with the same exclusive-create the project lock already uses.
4. **Writing becomes remote spend on the host's key.** The identity M6 items 1 and 3 needed was already sitting in the verified token; `[mcp].spend_scopes` and `[mcp].spend_rate_limit_per_hour` bind a synthesizing `add_source` to it — refused before anything is written for a missing scope or an exhausted per-identity hourly limit.
5. **`verify_concept`, for multiple humans reviewing one bundle.** Appends `{by: "human:<the caller's own verified identity>", at}` to a concept's `verified` list — never a string the caller supplies, never touching `status`. Gated by `[mcp].verify_scopes`, offered only under `--write --transport http`, and absent (not present-and-refusing) everywhere else.

### Next

- **Calibrate τ on a second corpus.** The first one saturated the similarity space, which rules the default out without ruling anything in. A corpus spanning more than one topic is what distinguishes "raise τ" from "lower k" from "make the threshold relative".
- **`POST /api/ingest` + SSE progress.** Deferred with M3, not cancelled. It needed a job registry and progress events `pipeline.ingest` did not emit; [the events landed with the Python API](#driving-it-from-python), the job registry did not.
- **Grounded answers** (M11, proposed). `search` normalizes by the best hit, so its top result always scores `1.000` — including for a question the bundle cannot answer. Absolute confidence with an abstention floor, offline citation checking, and trust tiers carried into results.
- **Distribution** (M5, implementation in place; exit criterion open). The rename and the console script have landed, and so has the half that makes a foreign checkout a consumer: `okragged mcp --project <dir>`, a `.mcp.json` entry naming nothing but a command on `PATH`, and the contract served as `okragged://contract` / `okragged://index` rather than as a file only the producer can open. So have `add_source` replacing `ingest` on the MCP surface, [the Python API](#driving-it-from-python), and [the lock and per-source commit](#one-writer-and-a-commit-per-source) that three writers to one bundle require. What is left is the exit criterion itself — a session driven from a checkout that is not the producer's, which nobody has had yet.
- **An identity, and a spend scope** (M6, the security layer — formally 3 of 5 items built). What exists under plain M5/M6 stdio: `okragged mcp` serves read-only and reads no `.env` unless asked, and `--write --spend` gives you back an `add_source` that authors the concept in the same call, resting on nothing but "you launched it" — caller and payer are the same person there, so there is no identity to bind a scope or a rate limit to and no counter a caller couldn't just delete. Items 1 and 3 stay unbuilt for that single-party case on purpose; they landed instead as **M8 criteria 1 and 4**, the moment a channel exists with someone else on the far end and a host is holding the only key.
- **Publish** (M7, packaging done and locally verified; exit criterion open). The graph view now reaches both the wheel and the sdist via a hatchling `artifacts` glob rather than a build hook — Node is needed only to *cut* a release, never to install one — and the `sources/` gitignore-anchoring defect that had been silently dropping that module from every wheel is fixed. `LICENSE` and the rest of the publish metadata exist. `okragged build-view` / `okragged serve --all` also shipped here, promoted from a staged idea once M7 made it real. A built sdist and wheel have been inspected directly for both fixes, and a throwaway venv has installed the wheel and found the view with no Node present — what is left is `pip install okragged` against the real index, which is the maintainer's own step.
- **Per-concept scoping on a hosted instance.** Deciding *what* a given consumer may see is separate from *whether* they may connect at all, which is all M8 built. Staged, not committed — and it would have to stay coarse if it lands, since the indexes already carry every child's `title` and `description` verbatim.
- **BigQuery/blob sources.** M9.

### Known rough edges in segmentation

- **Concept ids get long.** They come from model-written titles, so a parent plus child path can reach ~150 characters. Under Windows `MAX_PATH` that is tight but currently works; deeper nesting or longer titles would not.
- **Section titles occasionally drift language** on a Spanish corpus. Prompt calibration is deliberately post-M2 (`DESIGN.md` §13) because few-shot examples should come from a human-promoted `stable` concept, not from a guess.
- **okflint still reports `S202` split candidates** on some children. Segmentation cut the documents; it did not cut them as finely as okflint's cohesion clustering would.
- **A footnote label can resolve to nothing.** A generated concept was observed carrying `[^ley-26388]` while its `sources` declared only `id: delitos-2`. Footnote labels are the join key into `sources` (§5.1), so a label matching no declared id silently breaks per-claim attribution. `ensure_footnote_defs` only checks that a referenced footnote has a *definition*, not that its label is a declared source id, and no `W`-code covers it — okflint passes such a bundle. Worth a check in both `synth` and `okf/validate.py`.
