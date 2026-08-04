# ragged

Turns a folder of files into a conformant **Open Knowledge Format (OKF) v0.2** bundle — a directory of markdown concepts with YAML frontmatter that agents and humans can both read, with no SDK required.

**Status: M1 (walking skeleton).** One source file becomes one concept. Segmentation, cross-link weaving, per-directory indexes, incremental re-ingest, and the vector graph are not implemented yet — see [Not implemented yet](#not-implemented-yet).

- Architecture, data model and rationale: [`DESIGN.md`](DESIGN.md)
- **Roadmap and milestones (M1–M5): [`DESIGN.md` §13](DESIGN.md#13-milestones)**

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

This installs the `ragged` console script plus `okflint` (the OKF linter) and `pytest`. Runtime dependencies: `anthropic`, `google-genai`, `markitdown[all]`, `pydantic`, `python-dotenv`, `pyyaml`, `typer`.

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

Only one pipeline stage — synthesis — calls a model. Set which one in `ragged.toml`:

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

Without a credential, `ragged build --dry-run`, `ragged extract`, and `ragged check` all still work — they never call a model. `ragged build` fails immediately with a pointer to `.env` rather than after extracting your whole corpus.

`.env` is gitignored; `.env.example` is the committed template.

---

## Usage

```powershell
ragged doctor                # config, credential, provider reachability, model availability
ragged build --dry-run       # extract + report only. No model calls, no writes.
ragged build                 # full pipeline -> bundle/ + okf-base.yaml
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

Start with two or three files. The first real run is where the synthesis prompt gets tuned; there is no incremental re-ingest yet, so every `build` re-synthesizes everything and re-pays every model call.

### Exit codes

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `doctor` | provider reachable, model available | no credential, unreachable, or model unavailable | unknown provider name |
| `build` | all files synthesized, bundle conformant | a file failed, or the bundle is not conformant | no `sources/` directory, or no credential |
| `check` | bundle conformant | not conformant | no `bundle/` directory |
| `extract` | printed | — | file not found |

---

## How it works

```
discover → extract → synthesize → emit → validate
   │          │           │          │        │
 hash      cached      Claude    atomic    builtin
 files    by hash    (1 call     writes    checks +
                     per file)             okflint
```

| Stage | What happens |
|---|---|
| **discover** | Walk `sources/` against the `include`/`exclude` globs. SHA-256 each file. |
| **extract** | Bytes → markdown-ish text. `.md`/`.txt`/`.markdown` pass through (read as `utf-8-sig`); everything else goes through markitdown. Results are cached in `.ragged/extracted/<sha256>.json`, so re-runs cost nothing here. An extraction failure is recorded and reported — it does not abort the run. |
| **synthesize** | One model call per file through the configured provider, with **native schema enforcement** so the result is guaranteed rather than parsed out of prose. The system prompt is a frozen prefix (cached on Anthropic); only the document text varies. A safety decline or a token-cap stop becomes a per-file error, never an aborted run. |
| **emit** | Assemble frontmatter, write the concept, then the bundle-root `index.md` (declaring `okf_version: "0.2"`), append to `log.md` under today's ISO date newest-first, and generate `okf-base.yaml`. All writes are atomic (temp file + rename). |
| **validate** | Built-in conformance checks, then okflint as the normative gate. |

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
x_ragged_source_hash: 92933ee5...   # producer extension; drives future re-ingest
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
| `src/ragged/pipeline.py` | Orchestrates the five stages; assembles `Concept` objects from synthesis results. |
| `src/ragged/config.py` | Loads `ragged.toml` into pydantic models. Owns the `bundle/`, `sources/`, `.ragged/` path resolution. |
| `src/ragged/sources/base.py` | The `Source` protocol and `SourceItem`. Defines the `document` vs `asset` family split that phase-2 connectors plug into. |
| `src/ragged/sources/local.py` | `LocalFileSource` — the only implemented source. Globbing and hashing. |
| `src/ragged/extract/__init__.py` | Bytes → text, with the content-hash cache. markitdown plus passthrough. |
| `src/ragged/synth/prompt.py` | **The synthesis system prompt.** Must stay byte-identical across a run or prompt caching stops working. Tune concept quality here. |
| `src/ragged/synth/__init__.py` | Provider dispatch plus everything shared: prompt assembly, JSON parsing, required-field checks, footnote repair. |
| `src/ragged/synth/base.py` | The concept JSON schema, `SynthResult`, and the error types (`Refused`, `MissingCredential`, `SynthError`). |
| `src/ragged/synth/providers/claude.py` | Anthropic: `output_config.format`, cached system block, `stop_reason` handling. |
| `src/ragged/synth/providers/gemini.py` | Gemini: `response_json_schema`, `system_instruction`, `finish_reason` / `block_reason` handling. |
| `src/ragged/okf/concept.py` | The OKF data model. `render` / `parse` round-trip, unknown-key preservation, `slugify`. |
| `src/ragged/okf/writer.py` | Atomic concept writes, root `index.md`, `log.md` date-section insertion. |
| `src/ragged/okf/validate.py` | Conformance checks and the okflint subprocess. |
| `src/ragged/okf/manifest.py` | Generates `okf-base.yaml`. Documents why each key is present or omitted. |

### Config and output

| Path | Committed? | What it is |
|---|---|---|
| `ragged.toml` | yes | Configuration. See below. |
| `.env.example` | yes | Template for the API key. Copy to `.env`. |
| `.env` | **no** (gitignored) | Your API key. Read only by `build` and `doctor`. |
| `sources/` | your call | Layer 1 — raw input. **Immutable**; ragged reads, never writes. |
| `bundle/` | recommended | Layer 2 — the OKF product. The deliverable. Portable with no ragged dependency. |
| `okf-base.yaml` | yes | Generated okflint manifest. |
| `.ragged/` | **no** (gitignored) | Sidecar: extraction cache, and later embeddings, index, layout. Entirely derived and rebuildable. |
| `DESIGN.md` | yes | Architecture, data model, risks, milestones. |

The separation matters: **nothing derived ever goes inside `bundle/`.** A bundle must stay something you can zip up, hand to someone who has never heard of ragged, and read with `cat`. That portability is the whole point of OKF, and an embeddings file or an `embedding:` frontmatter key would destroy it.

### `ragged.toml`

```toml
[bundle]
root = "bundle"            # output directory
okf_version = "0.2"        # written into the bundle-root index.md
actor = "ragged/0.1.0"     # written as generated.by

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
```

`type` is a free-form string in OKF and the spec forbids imposing a taxonomy, so this vocabulary is per-bundle consistency rather than a standard. If your corpus has a domain, replacing these defaults up front produces a much better bundle than discovering types file by file.

---

## Tests

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"   # so okflint is discoverable
.\.venv\Scripts\python.exe -m pytest tests -q
```

53 tests. No credentials and no network needed — the end-to-end test stubs the model and asserts the emitted bundle is conformant, so "one file in, one conformant concept out" is covered offline.

| File | Covers |
|---|---|
| `tests/test_okf.py` | render/parse round-trip, extension-key preservation, `E1`/`E2`/`E3`, link resolution (absolute and relative), log ordering and date merging |
| `tests/test_pipeline.py` | end-to-end with a stubbed model, dry run, extraction-failure isolation, id collisions, new-type reporting, footnote repair |
| `tests/test_manifest.py` | manifest keys okflint requires, skip-with-reason behaviour, and cross-checking that okflint agrees with the built-in checks |
| `tests/test_providers.py` | provider registry and interface, dispatch, schema/required-field enforcement, Gemini credential resolution and decline mapping, fail-fast pre-flight |
| `tests/test_env.py` | `.env` loading with and without a BOM, and every `doctor` outcome |

Tests that need okflint skip cleanly when it is not on `PATH`.

---

## Platform notes

Three Windows issues hit during development, all fixed and worth knowing if you touch the relevant code. All three are the same underlying trap — Windows defaults that differ from UTF-8:

- **`.env` is read with `utf-8-sig`.** Notepad, VS Code, and PowerShell's `Set-Content -Encoding utf8` all write a BOM, which python-dotenv (UTF-8 by default) turns into a key literally named `﻿GEMINI_API_KEY`. The key is then silently invisible — `.env` reads as "found" while the credential reads as "missing".
- **Passthrough extraction reads with `utf-8-sig`.** Otherwise a BOM rides into the synthesis prompt as a leading invisible character.
- **Subprocess output is decoded as UTF-8 explicitly.** `subprocess.run(text=True)` inherits the locale codepage (cp1252); okflint emits characters outside it, the decode raises inside the reader thread, and `stdout` comes back as `None`.

---

## Not implemented yet

M1 is the walking skeleton. Full roadmap with exit criteria in [`DESIGN.md` §13](DESIGN.md#13-milestones). Missing, in priority order:

- **Segmentation.** One concept per source file regardless of length, so a long PDF becomes one oversized concept. `DESIGN.md` §4.3 has the intended policy.
- **`manifest.json` and incremental re-ingest.** Every `build` currently re-synthesizes everything and re-pays every model call. This is the biggest practical gap.
- **Link weaving.** Concepts are emitted unlinked, so the bundle is a flat set rather than a graph.
- **Per-directory `index.md`.** Only the bundle-root index is generated (hence `W4` warnings on subdirectories).
- **Human-edit protection.** Nothing yet detects a hand-edited or `verified` concept before overwriting it.
- **MCP server + generated `AGENTS.md`** (M4) — how a reading session consumes the bundle with no credential. This is the point of the project.
- **Vectors, graph UI, HTTP API, BigQuery/blob sources.** M3 and M5.
