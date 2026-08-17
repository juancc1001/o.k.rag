# Next steps — candidate improvements

**Status: none of this is scheduled.** `DESIGN.md` §13 is the authority on what is committed to; this file is the staging area in front of it. An item here is an idea with a stated reason, not a plan. **Promoting an item means moving it into `DESIGN.md` §13 as a milestone item and deleting it from here** — an idea that lives in two places drifts in one of them.

Numbers are identifiers, not an ordering: **2 (concurrency) and 8 (serving the contract over the protocol) were promoted into §13 M5 and M6**, and their numbers are not reused, so a reference to "item 5" means the same item it always did.

Most of what follows came from reading [`@llm-wiki/sdk`](https://github.com/stellarlinkco/llm-wiki) (TypeScript, MIT, v0.1.0 unpublished, commit `7c85ee6` of 2026-07-20), the closest existing implementation of this pipeline. Where an item is already solved there, that is noted — a design validated by a second implementation is worth more than one reasoned about alone.

---

## 1. Bundle store adapters

Today `okf/writer.py` writes a directory and nothing else can be true. `@llm-wiki/sdk` puts a `BundleStore` interface in front of storage — `snapshot`, `read`, `commit`, `listMarkdownPaths`, `exportTo` — with filesystem, SQLite, and Postgres implementations behind it, plus a separate `SearchAdapter` so BigQuery can serve search over a bundle it does not own.

**Why it's tempting.** It makes the bundle addressable from a service without a filesystem, and it separates "what a bundle is" from "where its bytes are" — which is the same separation `okf/` already wants for M8.

**Why it is not obviously right here, and this is the part to resolve first.** The load-bearing property of an okragged bundle is that you can `tar` it and `cat` it with no knowledge of okragged. A Postgres-backed bundle does not have that property; it has an *export* that does. So an adapter layer either demotes the directory from "the product" to "one serialization", or it admits the directory is canonical and everything else is a projection. Those are different products. **Decide that before writing the interface, not after.**

**Note on the axis.** This is not `DESIGN.md` §9 / M7. §9 is the `Source` protocol — where knowledge is read *from* (BigQuery, blob storage). This is where the bundle is written *to*. Two independent axes that both use the word "adapter"; keeping them separate in conversation avoids a real design confusion.

**Cost.** Touches `okf/writer.py`, `okf/validate.py`, `pipeline.py::_finalize`, and every test that asserts on paths. Not small.

**Gate.** Cleared — M4 shipped, and the MCP server is the second consumer it asked for. Now live as a question M5's Python API forces: an API that returns a `Bundle` object answers it silently, in the direction this item warns about. Decide it in M5 or refuse to, deliberately, by returning summaries and paths.

---

## 3. Guarded updates — refuse to silently destroy human work

**The strongest single idea in `@llm-wiki/sdk`, and the one closest to this project's existing instincts.** `okf-write-guards.ts` makes a concept update *fail* rather than apply when it would:

- drop or change existing frontmatter keys,
- drop a top-level heading present in the previous body,
- drop a citation the previous body carried,
- shrink a schema-like fenced block.

**Why this belongs here.** §4.4 and §6.4 already refuse to overwrite a concept whose body hash moved or that carries a `verified` block — the human's claim is detected at the level of *the whole file*. These guards work at the level of *what the rewrite does to the content*, which catches a case okragged currently misses: a concept the human has not touched, being rewritten by a later run in a way that quietly loses a section, a source citation, or a frontmatter key the human's tooling depends on.

Risk 6 in §12 is about human edits versus LLM ownership. This is the same risk one level down, and it is checkable offline — which by CLAUDE.md's own standard makes it a repair, not a prompt rule.

**Note.** The M2 commit "Fix what the real corpus found: three link errors and a lost section" is exactly the failure this class of guard catches automatically. That bug was found by reading the corpus output; a guard would have raised it.

---

## 4. Path confinement

`bundle-path.ts` in `@llm-wiki/sdk` validates every bundle-relative path before it touches the filesystem: rejects absolute paths, drive letters, `..` segments, embedded NULs, Windows reserved device names (`con`, `nul`, `lpt1`, …), trailing dots and spaces, and percent-encoded traversal decoded up to three levels; it resolves symlinks with `realpath` and asserts the result stays inside the approved root.

**Why it matters here.** Concept ids derive from **model-written titles**. That is untrusted input reaching a path constructor. A title containing `..`, a colon, or the word `nul` is not adversarial — it is a plausible accident of a Spanish legal corpus meeting a slugifier — and on Windows the reserved-name case fails in a confusing way rather than a loud one.

okragged has `tests/test_encoding.py` walking the AST to enforce the I/O encoding policy. There is no equivalent guard on path construction, and the reasoning that justified the encoding test applies unchanged: the platform default is wrong, and a deviating call site should fail the suite rather than fail a user.

**Cost.** Small and self-contained — one module in `okf/`, plus the AST-style test convention already established.

---

## 5. Connector snapshot semantics (feeds M7 / §9)

`syncSources` in `@llm-wiki/sdk` reconciles a caller-owned namespace against a list of observed sources, and **absence can only delete when the caller explicitly asserts `completeSnapshot: true`.**

**Why to record it now.** The ledger already marks vanished sources `status: deprecated`. That is correct for `LocalFileSource`, where a full directory sweep genuinely observes everything. It is wrong the moment a connector returns a *page* of results, or fails halfway, or is scoped to a prefix — absence then means "not in this response", not "gone". §9 designs the `Source` protocol now and implements it later; this is a piece of that protocol that is cheap to get right up front and expensive to retrofit, because retrofitting it means auditing every existing `deprecated` mark.

**Concretely:** `discover()` returning an iterator does not say whether exhausting it constitutes a complete observation. It should.

---

## 6. Prompt calibration

Already in `DESIGN.md` §13 and deliberately deferred until after M2 — **now unblocked**, and listed here only so it is not forgotten. M2, M3 and M4 have all shipped without it.

The method is unchanged and depends on a human, not on code: promote a generated concept from `draft` to `stable`, then use *that* concept as the few-shot example. Until someone promotes one, the correct action is still to leave prompt wording alone and prefer deterministic repairs (`synth/weave.py::prune_links`) over prompt rules, because a repair is testable and a prompt rule is not.

---

## 7. Operational — two accounts on one machine, and a 404 that means 403

Recorded because it cost an hour on 2026-08-08.

`git` and `gh` were both authenticating to `github.com` as `JuancrCarvallo`, while `origin` is `juancc1001/ragged` — private, and invisible to that account. **GitHub answers with 404, not 403, for a private repository the caller cannot see**, so `git fetch` reported `Repository not found` and `gh repo view` reported `Could not resolve to a Repository`. Both readings say "deleted". Neither was true: `origin/main` was at `128b089`, PR #2 merged at 21:05:39Z the same day.

The failure was diagnosed by `gh auth status` showing two logged-in accounts with the wrong one active, and confirmed by querying with the other account's token (`gh auth token --user juancc1001`). Fixed with `gh auth switch --user juancc1001`.

**Worth keeping in mind:** on this machine a "repository not found" is an authentication result until proven otherwise. Check `gh api user --jq '.login'` against the remote's owner before concluding anything about the remote's existence.

**Separately, and still true:** local `main` was 19 commits behind and a `git checkout main` emptied the working tree of every tracked file, which is how this started. That is normal git behaviour, but it is worth noting that `bundle/`, `sources/`, and `.okragged/` are gitignored by design (§13 — ids churn every rebuild, so the diff is unreadable), which means git protects the code and none of the corpus. **The corpus needs its own backup story, separate from the repository.** `HANDOFF-M2.md` was untracked, was never committed, and did not survive.

---

## 9. Per-concept read scoping

Considered while planning M6 and **deliberately left here.** §13 M6 settles *whether* a consumer may connect, with the bundle reachable only through tools. It does not settle *what* a given consumer may see, and nothing needs that yet: a bundle a host is willing to serve is a bundle it is willing to serve.

**If it ever lands it has to stay coarse**, because the format is self-describing on purpose. Per-directory `index.md` files carry every child's `title` and `description` verbatim, cross-links point anywhere in the tree, and `AGENTS.md` publishes the type vocabulary with counts. Serving the map while withholding the bodies still discloses the map — so the honest unit is a subtree, which M2 already made a valid entry point by giving every directory its own index. `--root finance/` is close to free; per-concept ACLs fight the grain and would half-work, which is worse than not offering them.

**Not negotiable either way:** access control never enters the bundle (§13 M6). Whatever this becomes, it is a server concern.

---

## 10. Addressing — a bundle has no name, only a location

**The observation.** MCP has no discovery: no registry, no broadcast, nothing a client can ask. A server exists to a client because a human wrote it into config, full stop. That is the protocol and not a defect. But it means every way of reaching a bundle is currently a *location*, and locations are not identities.

- **M5** addresses a bundle by filesystem path (`--project /some/where`). Machine-specific; meaningless in a message to someone else.
- **M6** would address it by URL. Host-specific; dead when the host moves or the tunnel restarts.
- Two copies of one bundle are indistinguishable from two unrelated bundles. `[bundle].title` is a display string, `okf-base.yaml` names the linter's manifest, and the root `index.md` declares `okf_version` — **none of them identifies the bundle.**

**Why it matters, concretely.** The intended flow is "someone runs okragged over their corpus and exposes it when they want to". For a second person to reach it, they need something to put in `claude mcp add`. Today the only candidates are a path on a machine they do not have or a URL that is valid until it is not. There is no sentence of the form *point me at that corpus* that survives the corpus moving.

**What registration ergonomics do and do not fix.** `claude mcp add <name> -- okragged mcp --project <path>` is a better line for a human than editing JSON, and `AGENTS.md` should probably print it next to the portable entry. It is worth doing and it is not this item: it shortens the instruction, it does not make the target nameable, and it is one client's command, so it cannot be the only form offered.

**Three questions this splits into, and they have different urgencies.**

1. **Does a bundle get a stable id?** The expensive question, and the one to settle early, because it is a public identifier in the same class as the `okragged://` scheme (§13 M5) — reversing it breaks everyone who recorded one. If yes, it belongs in the bundle-root `index.md`, since that is the one file the format already gives frontmatter, and it must be *generated once and never rewritten*, unlike concept ids which churn on every rebuild by design.
2. **Can one server serve more than one bundle?** `--project` is one root per process. Hosted, `https://host/<bundle>` is the obvious shape and is not designed. Cheap to answer, but it depends on (1) for what `<bundle>` is.
3. **How does a consumer find out a bundle exists at all?** Out of scope for anything soon — that is a directory service, and it is a different product.

**On the hosted answer, since it is the obvious one.** The natural fix is a URL per bundle — `https://…/<owner>/<bundle>` — with a reverse proxy routing each to whatever serves it. The routing is well-understood and is the easy half. Three things are worth writing down before anyone builds it:

- **"A library or a web service" is a false choice, and this design already dissolved it.** Layer 2 is a directory and the sidecar rule keeps everything derived out of it, so one bundle can be a folder someone tars, a local stdio server, a Python import and a URL behind a proxy *at the same time*. Shipping a library forecloses nothing, which is why M5 precedes M6 rather than competing with it: you cannot host what is not installable and cannot be pointed at an explicit project.

- **A URL is a better location, not an identity.** It survives the consumer's machine, which a filesystem path does not. It does not survive the service, and it makes whoever runs the service the source of the name — so the same bundle then has two locations, its owner's disk and that URL, with nothing in the bundle asserting they are the same thing. Question (1) does not go away here; it takes more load.

- **The routing unit and question (2) are one decision seen from two sides.** `--project` is one root per process, so per-bundle routing implies one process per bundle: simple, blast-radius-isolated, and wasteful across many small bundles. One process serving several changes what `okragged mcp` is. Deciding it once, as *how a server addresses bundles*, is cheaper than deciding it twice — as routing and as CLI shape — and finding out later that the two disagree.

**The cheap version, which is probably the right first one.** A per-bundle URL does not require multi-tenancy. One owner, one machine, one DNS name they control, a tunnel — that is "live on the web" without becoming a service provider, and it is what "expose it when I want" means in practice. Multi-tenant routing is for when other people's corpora live on your hardware, which is a company rather than a feature: the proxy is a weekend, and M6's five criteria plus uptime, backups and someone else's private legal corpus on your disk are permanent. §12 Risk 7 stops being a design note at that point and becomes a question someone's lawyer asks.

**The trap to avoid.** Do not let (1) become an `access:`-style key or a registry the bundle depends on to be readable. The sidecar rule's logic applies: a bundle must stay something you can `tar` and `cat`. An id is a *fact about* the bundle written into it; a lookup a reader must perform to understand it is not.

**Gate.** (1) before M6 ships anything anyone registers against. (2) with M6. (3) not scheduled, and possibly never here.

**Note on the intermittent case.** "Exposed when I feel like it" is usually a tunnel, and a tunnel URL is public the instant it exists — which means M6's authentication is not optional even for a bundle its owner thinks of as temporarily shared. That is worth stating because intermittent hosting *feels* lower-stakes than permanent hosting and is not.
