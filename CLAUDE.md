# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

`DESIGN.md` is the authority on intent (architecture, data model, §13 milestones with exit criteria). `README.md` describes current behaviour. **Both are deliberately kept current — a change that alters behaviour updates them in the same commit**, including the test count in the README.

Python 3.12+. Windows is the primary dev platform; commands below use the repo-local venv.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"   # installs okflint + pytest too

.\.venv\Scripts\ragged doctor          # provider, credential, reachability, model availability
.\.venv\Scripts\ragged build --dry-run # discover + extract only; no model calls, no writes
.\.venv\Scripts\ragged build           # full pipeline -> bundle/ + okf-base.yaml
.\.venv\Scripts\ragged check           # validate an existing bundle without rebuilding
.\.venv\Scripts\ragged extract <file>  # print extracted text for one file (offline)
```

Tests — okflint must be on `PATH` or the okflint-dependent tests skip:

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
.\.venv\Scripts\python.exe -m pytest tests -q              # 99 tests
.\.venv\Scripts\python.exe -m pytest tests/test_segment.py -q
.\.venv\Scripts\python.exe -m pytest tests -q -k "footnote"
```

No credentials and no network are needed for the suite; the end-to-end test stubs the model.

## Architecture

Three layers plus a sidecar (`DESIGN.md` §2):

| | Directory | Owner | Notes |
|---|---|---|---|
| 1 raw sources | `sources/` | user | immutable; ragged reads, never writes |
| 2 the bundle | `bundle/` | ragged | the OKF v0.2 product |
| 3 agent contract | `agent/` | ragged | M4, not built |
| sidecar | `.ragged/` | ragged | derived, disposable |

**The sidecar rule is the load-bearing constraint: nothing derived ever goes inside `bundle/`.** Extraction cache, content hashes, and later embeddings/index/projection all live in `.ragged/`. A bundle must stay something you can `tar` up and `cat` with no knowledge of ragged — no `embedding:` frontmatter key, no `.faiss` file.

Pipeline (`pipeline.py::ingest`): `discover → extract → plan → synthesize → emit → validate`.

**Only synthesis calls a model.** `check`, `extract`, and `build --dry-run` are entirely offline, and `build` fails its credential pre-flight *before* extracting the corpus rather than after.

Module responsibilities:

- `okf/` — the format layer, and the only part with standalone value (M6 candidate). Bound to the OKF spec, contains no model call. **Keep it free of any dependency on `synth/` or `pipeline.py`.**
- `synth/` — provider dispatch. Everything shared (prompt assembly, JSON parse, required-field checks, footnote repair) lives in `synth/__init__.py` and `synth/base.py` so the provider paths cannot drift. Provider modules stay thin: auth, request shape, decline detection.
- `sources/base.py` — the `Source` protocol with its `document` vs `asset` family split; phase-2 connectors plug in here.
- `pipeline.py` — orchestration only; assembles `Concept` objects from synthesis results.

Adding a provider means one module under `synth/providers/` exposing `NAME`, `DEFAULT_MODEL`, `ENV_VARS`, `credential_present()`, `list_models()`, `call()`, registered in `providers/__init__.py::PROVIDERS`. Both current providers enforce the concept schema natively (Anthropic `output_config.format`, Gemini `response_json_schema`) — output shape is guaranteed, never parsed out of prose.

## Invariants that are easy to break

**Encoding.** Windows defaults to cp1252, and five separate M1 bugs came from trusting a platform default at an I/O boundary. Reads use `encoding="utf-8-sig"`, writes use `encoding="utf-8", newline="\n"`, subprocesses decode `utf-8`/`errors="replace"` *and* set `PYTHONIOENCODING=utf-8` in the child's `env`. **`tests/test_encoding.py` enforces this by walking the AST of everything under `src/ragged/` — a deviating call site fails the suite.** README's *Encoding policy* table is the full rule set.

**`verified` is never written.** It records a confirmation event that actually happened; writing it because content looks correct forges the human-reviewed trust tier. Concepts are emitted `status: draft` and a human promotes them by editing the file.

**System prompts must stay byte-identical across a run** (`synth/prompt.py`), or Anthropic prompt caching stops working. Section framing goes in the user message, never the system prefix.

**Per-unit failures never abort a run.** A refusal, a token-cap stop, or any non-2xx becomes an error recorded against that file or section; the sections that did synthesize are kept. `_finalize` (index, log, manifest) runs in a `finally` so a Ctrl-C cannot strand model calls already paid for. Preserve this when adding stages.

**One model call per unit, each with its own `max_tokens`.** A single call returning an array makes every concept share one budget — that is what capped M1 at ~2.5k per concept regardless of source length.

**New `type` values are reported, never silently accepted.** They surface in the build summary for the owner to add to `ragged.toml`.

**Broken cross-links are warnings (`W2`), not errors.** The spec permits pointing at knowledge not yet written.

**`okf-base.yaml` is deliberately minimal** — no `profile` block (it would reject newly proposed types) and no `okf_version` key (okflint 0.3.1 recognises only `0.1`). `okf/manifest.py` documents why each key is present or omitted. Note this is the *okflint* manifest; the planned idempotency ledger (`.ragged/manifest.json`, `DESIGN.md` §5) is a different artifact needing its own module name.

**`bundle/`, `sources/`, `.ragged/`, `okf-base.yaml` and `.env` are all gitignored.** Concept ids derive from model-written titles, so every rebuild renames the whole tree and the diff is unreadable; a bundle worth showing goes under `examples/`, refreshed deliberately.

## Current state

M1 done. M2 is item 2 of 4 (segmentation) — see `DESIGN.md` §13 for the remaining items in dependency order and their exit criteria. In short: incremental re-ingest (`manifest.json`) blocks the rest, then link-weave, then the general per-directory index pass.

**Prompt calibration is deliberately deferred until after M2** (§13). Few-shot examples are the strongest signal in a prompt, and writing them now would bake in guesses about a unit shape segmentation just changed. The method afterwards is to use a concept a human promoted from `draft` to `stable` as the example. Don't "improve" prompt wording opportunistically.

## Tests

Behavioural test names (`test_a_bom_in_a_concept_does_not_read_as_missing_frontmatter`), model always stubbed, no network. `tests/test_encoding.py` includes a meta-test pointing the AST check at a deliberately bad file — a guard that always passes guards nothing. Follow both conventions.
