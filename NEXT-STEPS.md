# Next steps — candidate improvements

**Status: none of this is scheduled.** `DESIGN.md` §13 is the authority on what is committed to; this file is the staging area in front of it. An item here is an idea with a stated reason, not a plan. **Promoting an item means moving it into `DESIGN.md` §13 as a milestone item and deleting it from here** — an idea that lives in two places drifts in one of them.

Most of what follows came from reading [`@llm-wiki/sdk`](https://github.com/stellarlinkco/llm-wiki) (TypeScript, MIT, v0.1.0 unpublished, commit `7c85ee6` of 2026-07-20), the closest existing implementation of this pipeline. Where an item is already solved there, that is noted — a design validated by a second implementation is worth more than one reasoned about alone.

---

## 1. Bundle store adapters

Today `okf/writer.py` writes a directory and nothing else can be true. `@llm-wiki/sdk` puts a `BundleStore` interface in front of storage — `snapshot`, `read`, `commit`, `listMarkdownPaths`, `exportTo` — with filesystem, SQLite, and Postgres implementations behind it, plus a separate `SearchAdapter` so BigQuery can serve search over a bundle it does not own.

**Why it's tempting.** It makes the bundle addressable from a service without a filesystem, and it separates "what a bundle is" from "where its bytes are" — which is the same separation `okf/` already wants for M6.

**Why it is not obviously right here, and this is the part to resolve first.** The load-bearing property of a ragged bundle is that you can `tar` it and `cat` it with no knowledge of ragged. A Postgres-backed bundle does not have that property; it has an *export* that does. So an adapter layer either demotes the directory from "the product" to "one serialization", or it admits the directory is canonical and everything else is a projection. Those are different products. **Decide that before writing the interface, not after.**

**Note on the axis.** This is not `DESIGN.md` §9 / M5. §9 is the `Source` protocol — where knowledge is read *from* (BigQuery, blob storage). This is where the bundle is written *to*. Two independent axes that both use the word "adapter"; keeping them separate in conversation avoids a real design confusion.

**Cost.** Touches `okf/writer.py`, `okf/validate.py`, `pipeline.py::_finalize`, and every test that asserts on paths. Not small.

**Gate.** Do not start before M4. A second consumer of `okf/` (the MCP server) is exactly the evidence M6 asks for, and it will say more about the right interface than speculation does.

---

## 2. Concurrency and atomic commit

Ragged assumes one process, one run. Nothing stops two `ragged build` invocations from interleaving writes into `bundle/`, and nothing detects it afterwards.

`@llm-wiki/sdk` solves this with optimistic concurrency: every mutation carries an `expectedRevision`, a stale one raises `RevisionConflictError` *before* anything becomes visible, and commits stage into a temporary prefix with a lock file and a pending-commit record so a crash mid-write is recoverable rather than half-applied.

**Why it matters here specifically.** `_finalize` already runs in a `finally` so a Ctrl-C cannot strand paid-for model calls. That protects the *run*; it does not protect the *bundle*. A Ctrl-C during emit currently leaves a partially rewritten tree, and because parent ids derive from model-written titles, a partial rewrite is not a subset of the old tree — it is a mix of two namings. The stale-output cleanup in the ledger assumes it is reconciling against a complete emit.

**Cheapest useful version.** A lock file in `.ragged/` plus staged emit (write to `.ragged/staging/`, then swap) would remove the crash-mid-emit failure without introducing a revision model at all. Full optimistic concurrency is only worth it if something other than the CLI ever writes — which is item 1's question again.

---

## 3. Guarded updates — refuse to silently destroy human work

**The strongest single idea in `@llm-wiki/sdk`, and the one closest to this project's existing instincts.** `okf-write-guards.ts` makes a concept update *fail* rather than apply when it would:

- drop or change existing frontmatter keys,
- drop a top-level heading present in the previous body,
- drop a citation the previous body carried,
- shrink a schema-like fenced block.

**Why this belongs here.** §4.4 and §6.4 already refuse to overwrite a concept whose body hash moved or that carries a `verified` block — the human's claim is detected at the level of *the whole file*. These guards work at the level of *what the rewrite does to the content*, which catches a case ragged currently misses: a concept the human has not touched, being rewritten by a later run in a way that quietly loses a section, a source citation, or a frontmatter key the human's tooling depends on.

Risk 6 in §12 is about human edits versus LLM ownership. This is the same risk one level down, and it is checkable offline — which by CLAUDE.md's own standard makes it a repair, not a prompt rule.

**Note.** The M2 commit "Fix what the real corpus found: three link errors and a lost section" is exactly the failure this class of guard catches automatically. That bug was found by reading the corpus output; a guard would have raised it.

---

## 4. Path confinement

`bundle-path.ts` in `@llm-wiki/sdk` validates every bundle-relative path before it touches the filesystem: rejects absolute paths, drive letters, `..` segments, embedded NULs, Windows reserved device names (`con`, `nul`, `lpt1`, …), trailing dots and spaces, and percent-encoded traversal decoded up to three levels; it resolves symlinks with `realpath` and asserts the result stays inside the approved root.

**Why it matters here.** Concept ids derive from **model-written titles**. That is untrusted input reaching a path constructor. A title containing `..`, a colon, or the word `nul` is not adversarial — it is a plausible accident of a Spanish legal corpus meeting a slugifier — and on Windows the reserved-name case fails in a confusing way rather than a loud one.

Ragged has `tests/test_encoding.py` walking the AST to enforce the I/O encoding policy. There is no equivalent guard on path construction, and the reasoning that justified the encoding test applies unchanged: the platform default is wrong, and a deviating call site should fail the suite rather than fail a user.

**Cost.** Small and self-contained — one module in `okf/`, plus the AST-style test convention already established.

---

## 5. Connector snapshot semantics (feeds M5 / §9)

`syncSources` in `@llm-wiki/sdk` reconciles a caller-owned namespace against a list of observed sources, and **absence can only delete when the caller explicitly asserts `completeSnapshot: true`.**

**Why to record it now.** The ledger already marks vanished sources `status: deprecated`. That is correct for `LocalFileSource`, where a full directory sweep genuinely observes everything. It is wrong the moment a connector returns a *page* of results, or fails halfway, or is scoped to a prefix — absence then means "not in this response", not "gone". §9 designs the `Source` protocol now and implements it later; this is a piece of that protocol that is cheap to get right up front and expensive to retrofit, because retrofitting it means auditing every existing `deprecated` mark.

**Concretely:** `discover()` returning an iterator does not say whether exhausting it constitutes a complete observation. It should.

---

## 6. Prompt calibration

Already in `DESIGN.md` §13 and deliberately deferred until after M2 — **now unblocked**, and listed here only so it is not forgotten while M4 is in flight.

The method is unchanged and depends on a human, not on code: promote a generated concept from `draft` to `stable`, then use *that* concept as the few-shot example. Until someone promotes one, the correct action is still to leave prompt wording alone and prefer deterministic repairs (`synth/weave.py::prune_links`) over prompt rules, because a repair is testable and a prompt rule is not.

---

## 7. Operational — two accounts on one machine, and a 404 that means 403

Recorded because it cost an hour on 2026-08-08.

`git` and `gh` were both authenticating to `github.com` as `JuancrCarvallo`, while `origin` is `juancc1001/ragged` — private, and invisible to that account. **GitHub answers with 404, not 403, for a private repository the caller cannot see**, so `git fetch` reported `Repository not found` and `gh repo view` reported `Could not resolve to a Repository`. Both readings say "deleted". Neither was true: `origin/main` was at `128b089`, PR #2 merged at 21:05:39Z the same day.

The failure was diagnosed by `gh auth status` showing two logged-in accounts with the wrong one active, and confirmed by querying with the other account's token (`gh auth token --user juancc1001`). Fixed with `gh auth switch --user juancc1001`.

**Worth keeping in mind:** on this machine a "repository not found" is an authentication result until proven otherwise. Check `gh api user --jq '.login'` against the remote's owner before concluding anything about the remote's existence.

**Separately, and still true:** local `main` was 19 commits behind and a `git checkout main` emptied the working tree of every tracked file, which is how this started. That is normal git behaviour, but it is worth noting that `bundle/`, `sources/`, and `.ragged/` are gitignored by design (§13 — ids churn every rebuild, so the diff is unreadable), which means git protects the code and none of the corpus. **The corpus needs its own backup story, separate from the repository.** `HANDOFF-M2.md` was untracked, was never committed, and did not survive.
