# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

`DESIGN.md` is the authority on intent (architecture, data model, §13 milestones with exit criteria). `README.md` describes current behaviour. **Both are deliberately kept current — a change that alters behaviour updates them in the same commit**, including the test count in the README.

Python 3.12+. Windows is the primary dev platform; commands below use the repo-local venv.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"   # installs okflint + pytest + the MCP SDK

.\.venv\Scripts\okragged doctor          # both providers (synth + embeddings), credentials, reachability
.\.venv\Scripts\okragged build --dry-run # discover + extract only; no model calls, no writes
.\.venv\Scripts\okragged build           # full pipeline -> bundle/ + okf-base.yaml
.\.venv\Scripts\okragged index           # embed + project + graph.json -> .okragged/ only
.\.venv\Scripts\okragged serve           # read-only API + the graph view (needs the api extra)
.\.venv\Scripts\okragged check           # validate an existing bundle without rebuilding
.\.venv\Scripts\okragged agent           # regenerate agent/AGENTS.md + .mcp.json (offline)
.\.venv\Scripts\okragged mcp             # serve a bundle over stdio; --project / OKRAGGED_PROJECT
.\.venv\Scripts\okragged extract <file>  # print extracted text for one file (offline)
```

Tests — okflint is resolved beside the interpreter, so `PATH` no longer decides whether the okflint-dependent tests run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q              # 267 tests, none skipped
.\.venv\Scripts\python.exe -m pytest tests/test_segment.py -q
.\.venv\Scripts\python.exe -m pytest tests -q -k "footnote"
```

No credentials and no network are needed for the suite; the end-to-end test stubs the model.

## Architecture

Three layers plus a sidecar (`DESIGN.md` §2):

| | Directory | Owner | Notes |
|---|---|---|---|
| 1 raw sources | `sources/` | user | immutable; okragged reads, never writes |
| 2 the bundle | `bundle/` | okragged | the OKF v0.2 product |
| 3 agent contract | `agent/` | okragged | generated: `AGENTS.md` and `.mcp.json`; the server is `okragged mcp` |
| sidecar | `.okragged/` | okragged | derived, disposable |

**The sidecar rule is the load-bearing constraint: nothing derived ever goes inside `bundle/`.** The ledger (`manifest.json`), the extraction cache, proposed replacements for concepts a human owns, and later embeddings/index/projection all live in `.okragged/`. A bundle must stay something you can `tar` up and `cat` with no knowledge of okragged — no `embedding:` frontmatter key, no `.faiss` file.

Pipeline (`pipeline.py::ingest`): `discover → extract → plan → synthesize → link-weave → emit → validate`. Then, as a separate command, `vectors.index`: `embed → project → graph`. `_finalize` also regenerates the layer-3 contract, so it can never describe a bundle that no longer exists.

**In `build`, only synthesis and link-weave call a model.** `check`, `extract`, and `build --dry-run` are entirely offline, and `build` fails its credential pre-flight *before* extracting the corpus rather than after. `index` calls an *embedding* provider, configured separately in `[vectors]`, and `build` never calls it — nothing in the build path uses a vector.

Module responsibilities:

- `okf/` — the format layer, and the only part with standalone value (M8 candidate). Bound to the OKF spec, contains no model call. **Keep it free of any dependency on `synth/`, `pipeline.py`, `vectors/` or `agent/`.**
- `synth/` — provider dispatch. Everything shared (prompt assembly, JSON parse, required-field checks, footnote repair) lives in `synth/__init__.py` and `synth/base.py` so the provider paths cannot drift. Provider modules stay thin: auth, request shape, decline detection.
- `sources/base.py` — the `Source` protocol with its `document` vs `asset` family split; phase-2 connectors plug in here.
- `pipeline.py` — orchestration only; assembles `Concept` objects from synthesis results.
- `ledger.py` — the idempotency ledger, `.okragged/manifest.json`. Sidecar state, so every read path degrades to "no record" rather than raising. **Not `okf/manifest.py`**, which generates `okf-base.yaml` for okflint.
- `agent/` — layer 3 (`DESIGN.md` §10): `query.py` (`search`, `read_concept`), `lint.py`, `contract.py` (generates `agent/AGENTS.md` and the `.mcp.json` entry) and `server.py` (the four MCP tools plus the two resources, served by `okragged mcp`). **Nothing the server says over the wire may name a path relative to the producer's root** — the consumer works in another tree, which is why the contract is a resource rather than a file to open, and `tests/test_agent.py` enforces it. **Three of the four tools must keep working with no credential**, which is why `search` falls back from embedding kNN to index traversal. `server.py` imports the MCP SDK lazily — it is an optional extra — and `agent/__init__.py` deliberately does not import it, because `pipeline.py` imports this package.
- `api/` — the read-only HTTP half of layer 3 (`DESIGN.md` §11), behind `okragged serve`. Four endpoints, all offline, **none of which writes anywhere**. `search` and `lint` call the same functions `agent/` exposes over MCP rather than reimplementing them, so the view and an agent cannot be told different things about one bundle — preserve that when adding an endpoint. FastAPI is an optional extra imported inside `create_app`, so importing the package costs nothing without it. The two ingest endpoints of §11 are deliberately deferred.
- `web/` — the sigma.js view (Vite + React + TS). Positions come precomputed from `graph.json`; sigma is a renderer, never a layout engine. `web/src/encoding.ts` holds §8's visual encoding alone, so the legend and the renderer cannot drift. Filtering lives in sigma reducers rather than in graph rebuilds, or the τ slider reloads the graph on every pixel.
- `vectors/` — embed, project, kNN, `graph.json`. Writes only into `.okragged/`, and `okf/` must not depend on it any more than it depends on `synth/`. `vectors/providers/` mirrors `synth/providers/` module for module; which embedding provider is right is **an open question** (`DESIGN.md` §12 Risk 4) and both registered ones stay registered until a real corpus answers it.

Adding a provider means one module under `synth/providers/` exposing `NAME`, `DEFAULT_MODEL`, `ENV_VARS`, `credential_present()`, `list_models()`, `call()`, registered in `providers/__init__.py::PROVIDERS`. Both current providers enforce the concept schema natively (Anthropic `output_config.format`, Gemini `response_json_schema`) — output shape is guaranteed, never parsed out of prose.

## Invariants that are easy to break

**Encoding.** Windows defaults to cp1252, and five separate M1 bugs came from trusting a platform default at an I/O boundary. Reads use `encoding="utf-8-sig"`, writes use `encoding="utf-8", newline="\n"`, subprocesses decode `utf-8`/`errors="replace"` *and* set `PYTHONIOENCODING=utf-8` in the child's `env`. **`tests/test_encoding.py` enforces this by walking the AST of everything under `src/okragged/` — a deviating call site fails the suite.** README's *Encoding policy* table is the full rule set.

**Executables are resolved beside `sys.executable` before `PATH`.** `okf/validate.py::_okflint` does this, and the reason generalises: the MCP server is spawned by its `.mcp.json` entry rather than from an activated shell, so a console script installed into okragged's own venv is invisible to `shutil.which`. The symptom was silent and asymmetric — `lint` told an agent "okflint not installed" while `okragged check` in a terminal ran it, so the same bundle was validated to two different standards depending on who asked. Any future subprocess okragged owns has the same exposure.

**`verified` is never written.** It records a confirmation event that actually happened; writing it because content looks correct forges the human-reviewed trust tier. Concepts are emitted `status: draft` and a human promotes them by editing the file.

**System prompts must stay byte-identical across a run** (`synth/prompt.py`), or Anthropic prompt caching stops working. Section framing goes in the user message, never the system prefix. The concept index in the prefix is snapshotted *before* discovery for the same reason — letting it grow as concepts land would be more informative and would stop it caching after the first unit.

**Per-unit failures never abort a run.** A refusal, a token-cap stop, or any non-2xx becomes an error recorded against that file or section; the sections that did synthesize are kept. `_finalize` (indexes, log, okflint manifest, ledger) runs in a `finally` so a Ctrl-C cannot strand model calls already paid for. Preserve this when adding stages. One asymmetry to keep: a failed **section** marks the source `complete: false` in the ledger so the next run retries it, because that is source content missing from the bundle; a failed **weave** does not, because the concept is valid and only its links are missing.

**One model call per unit, each with its own `max_tokens`.** A single call returning an array makes every concept share one budget — that is what capped M1 at ~2.5k per concept regardless of source length.

**New `type` values are reported, never silently accepted.** They surface in the build summary for the owner to add to `okragged.toml`.

**Broken cross-links are warnings (`W2`), not errors.** The spec permits pointing at knowledge not yet written — *by a human*. The link-weave pass is handed the full concept index, so a path outside it is a guess, and `synth/weave.py::prune_links` unwraps it and reports the target. Two other model link errors are repaired there too (inverted syntax, a raw path used as the label). Repairs go in `prune_links`, not in the prompt: a repair is checkable offline, a prompt rule is not.

**`okf-base.yaml` is deliberately minimal** — no `profile` block (it would reject newly proposed types) and no `okf_version` key (okflint 0.3.1 recognises only `0.1`). `okf/manifest.py` documents why each key is present or omitted. Note this is the *okflint* manifest; the idempotency ledger (`.okragged/manifest.json`, `DESIGN.md` §5) is a different artifact and lives in `ledger.py`.

**`bundle/`, `sources/`, `.okragged/`, `agent/`, `okf-base.yaml`, `.mcp.json` and `.env` are all gitignored.** Concept ids derive from model-written titles, so every rebuild renames the whole tree and the diff is unreadable; a bundle worth showing goes under `examples/`, refreshed deliberately. `agent/` goes with it: `AGENTS.md` is generated from the bundle and carries the corpus's own names, and `.mcp.json` is generated beside it and merges with whatever else a machine already registers.

## Current state

M1 and M2 done — all four M2 items (segmentation, incremental re-ingest, link-weave, per-directory indexes), verified on the real corpus: conformant bundle, okflint exit 0, and a second `build` making zero model calls.

**M3 is done** — `embed`, `project` and `graph.json` behind `okragged index`, the four read endpoints behind `okragged serve`, and the sigma.js view in `web/`. See `DESIGN.md` §13, which lists M3 before M4 because that is the order the work happened in, not because the reason for the original swap stopped holding.

**τ has met a corpus and the default lost, which is the milestone's real output.** On the 18-concept Spanish corpus the median similarity over all 153 pairs is 0.896 and 150 of them clear τ = 0.82 — the 76 ghost edges are 76 because `knn_k = 8` capped them, not because τ discriminated. **The default has deliberately not been changed:** one saturated single-topic corpus rules 0.82 out without distinguishing "raise τ" from "lower k" from "make the threshold relative to the corpus", and fitting a constant to one sample is how a guess becomes a number nobody can argue with. Risk 1 in §12 records this. A second, less homogeneous corpus is what moves it.

**M4 (agent contract) is done** — `src/okragged/agent/`, generated on every build and by `okragged agent`. Its exit criterion was met against the real corpus over a real stdio session with no embedding credential: `search` answered by traversal, `read_concept` returned the concept whole, `lint` reported okflint exit 0. Now that `okragged index` has run, `search` takes the embedding path on this corpus and the traversal fallback is exercised only by the tests — **keep it that way; it is the path the exit criterion depends on.**

**M5 (distribution) is in progress.** The package, the module and the console script are all `okragged` (`ragged` on PyPI is taken), and **layer 3 now crosses the process boundary over the protocol rather than over the filesystem**: the server is `okragged mcp --project <dir>`, `.mcp.json` registers a console script with no interpreter path and no script path, the generated `agent/mcp_server.py` is gone, and `okragged://contract` / `okragged://index` serve the contract to a consumer that cannot see this tree. Verified over a real stdio session spawned from a foreign cwd. Still to come in M5: the Python API, `add_source` replacing `ingest` on the tool surface, and the lock plus staged emit.

**Prompt calibration was deferred until after M2** (§13) and is now unblocked. Few-shot examples are the strongest signal in a prompt, and writing them before segmentation settled would have baked in guesses about the unit shape. The method is to take a concept a human promoted from `draft` to `stable` and use *that* as the example — so it needs a human to promote one first. Until then, don't "improve" prompt wording opportunistically; a model failure that can be repaired deterministically (see `prune_links`) should be, because a repair is testable and a prompt rule is not.

## Tests

Behavioural test names (`test_a_bom_in_a_concept_does_not_read_as_missing_frontmatter`), model always stubbed, no network. `tests/test_encoding.py` includes a meta-test pointing the AST check at a deliberately bad file — a guard that always passes guards nothing. Follow both conventions.
