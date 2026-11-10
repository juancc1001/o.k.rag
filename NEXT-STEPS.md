# Next steps — candidate improvements

**Status: none of this is scheduled.** `DESIGN.md` §13 is the authority on what is committed to; this file is the staging area in front of it. An item here is an idea with a stated reason, not a plan. **Promoting an item means moving it into `DESIGN.md` §13 as a milestone item and deleting it from here** — an idea that lives in two places drifts in one of them.

Numbers are identifiers, not an ordering: **2 (concurrency) and 8 (serving the contract over the protocol) were promoted into §13 M5**, **12 (one command to build and serve the graph view) was promoted into §13 M7**, **5 (connector snapshot semantics) was promoted into §9's `Source` protocol**, and **11 (`okrag` on `PATH`) was closed by M7's publish**; their numbers are not reused, so a reference to "item 9" means the same item it always did.

Most of what follows came from reading [`@llm-wiki/sdk`](https://github.com/stellarlinkco/llm-wiki) (TypeScript, MIT, v0.1.0 unpublished, commit `7c85ee6` of 2026-07-20), the closest existing implementation of this pipeline. Where an item is already solved there, that is noted — a design validated by a second implementation is worth more than one reasoned about alone.

---

## 1. Bundle store adapters

Today `okf/writer.py` writes a directory and nothing else can be true. `@llm-wiki/sdk` puts a `BundleStore` interface in front of storage — `snapshot`, `read`, `commit`, `listMarkdownPaths`, `exportTo` — with filesystem, SQLite, and Postgres implementations behind it, plus a separate `SearchAdapter` so BigQuery can serve search over a bundle it does not own.

**Why it's tempting.** It makes the bundle addressable from a service without a filesystem, and it separates "what a bundle is" from "where its bytes are" — which is the same separation `okf/` already wants for M10.

**Why it is not obviously right here, and this is the part to resolve first.** The load-bearing property of an okrag bundle is that you can `tar` it and `cat` it with no knowledge of okrag. A Postgres-backed bundle does not have that property; it has an *export* that does. So an adapter layer either demotes the directory from "the product" to "one serialization", or it admits the directory is canonical and everything else is a projection. Those are different products. **Decide that before writing the interface, not after.**

**Note on the axis.** This is not `DESIGN.md` §9 / M9. §9 is the `Source` protocol — where knowledge is read *from* (BigQuery, blob storage). This is where the bundle is written *to*. Two independent axes that both use the word "adapter"; keeping them separate in conversation avoids a real design confusion.

**Cost.** Touches `okf/writer.py`, `okf/validate.py`, `pipeline.py::_finalize`, and every test that asserts on paths. Not small.

**Gate.** Cleared — M4 shipped, and the MCP server is the second consumer it asked for. M5's Python API forced the question and **refused it deliberately**: the public functions return the summaries and paths the underlying ones already returned, there is no `Bundle`, and `okrag.__init__` has no name that stands for a bundle. So this item is still open and still undecided — which is the point. Nothing published so far assumes the directory is one serialization among several.

---

## 3. Guarded updates — refuse to silently destroy human work

**The strongest single idea in `@llm-wiki/sdk`, and the one closest to this project's existing instincts.** `okf-write-guards.ts` makes a concept update *fail* rather than apply when it would:

- drop or change existing frontmatter keys,
- drop a top-level heading present in the previous body,
- drop a citation the previous body carried,
- shrink a schema-like fenced block.

**Why this belongs here.** §4.4 and §6.4 already refuse to overwrite a concept whose body hash moved or that carries a `verified` block — the human's claim is detected at the level of *the whole file*. These guards work at the level of *what the rewrite does to the content*, which catches a case okrag currently misses: a concept the human has not touched, being rewritten by a later run in a way that quietly loses a section, a source citation, or a frontmatter key the human's tooling depends on.

Risk 6 in §12 is about human edits versus LLM ownership. This is the same risk one level down, and it is checkable offline — which by CLAUDE.md's own standard makes it a repair, not a prompt rule.

**Note.** The M2 commit "Fix what the real corpus found: three link errors and a lost section" is exactly the failure this class of guard catches automatically. That bug was found by reading the corpus output; a guard would have raised it.

---

## 4. Path confinement

`bundle-path.ts` in `@llm-wiki/sdk` validates every bundle-relative path before it touches the filesystem: rejects absolute paths, drive letters, `..` segments, embedded NULs, Windows reserved device names (`con`, `nul`, `lpt1`, …), trailing dots and spaces, and percent-encoded traversal decoded up to three levels; it resolves symlinks with `realpath` and asserts the result stays inside the approved root.

**Why it matters here.** Concept ids derive from **model-written titles**. That is untrusted input reaching a path constructor. A title containing `..`, a colon, or the word `nul` is not adversarial — it is a plausible accident of a Spanish legal corpus meeting a slugifier — and on Windows the reserved-name case fails in a confusing way rather than a loud one.

okrag has `tests/test_encoding.py` walking the AST to enforce the I/O encoding policy. There is no equivalent guard on path construction, and the reasoning that justified the encoding test applies unchanged: the platform default is wrong, and a deviating call site should fail the suite rather than fail a user.

**Cost.** Small and self-contained — one module in `okf/`, plus the AST-style test convention already established.

---

## 6. Prompt calibration

Already in `DESIGN.md` §13 and deliberately deferred until after M2 — **now unblocked**, and listed here only so it is not forgotten. M2, M3 and M4 have all shipped without it.

The method is unchanged and depends on a human, not on code: promote a generated concept from `draft` to `stable`, then use *that* concept as the few-shot example. Until someone promotes one, the correct action is still to leave prompt wording alone and prefer deterministic repairs (`synth/weave.py::prune_links`) over prompt rules, because a repair is testable and a prompt rule is not.

---

## 7. A backup story for the corpus

`bundle/`, `sources/` and `.okrag/` are gitignored by design (§13 — ids churn every rebuild, so the diff is unreadable), which means git protects the code and none of the corpus. **The corpus needs its own backup story, separate from the repository.** Nothing provides one yet.

---

## 9. Per-concept read scoping

Considered while planning the hosted server and **deliberately left here.** §13 M8 settles *whether* a consumer may connect, with the bundle reachable only through tools. It does not settle *what* a given consumer may see, and nothing needs that yet.

**If it ever lands it has to stay coarse**, because the format is self-describing on purpose. Per-directory `index.md` files carry every child's `title` and `description` verbatim, cross-links point anywhere in the tree, and `AGENTS.md` publishes the type vocabulary with counts. Serving the map while withholding the bodies still discloses the map — so the honest unit is a subtree, which M2 already made a valid entry point by giving every directory its own index. `--root finance/` is close to free; per-concept ACLs fight the grain and would half-work, which is worse than not offering them.

**Not negotiable either way:** access control never enters the bundle (§13 M8). Whatever this becomes, it is a server concern.

---

## 10. Addressing — a bundle has no name, only a location

**The observation.** MCP has no discovery: no registry, no broadcast, nothing a client can ask. A server exists to a client because a human wrote it into config, full stop. That is the protocol and not a defect. But it means every way of reaching a bundle is currently a *location*, and locations are not identities.

- **M5** addresses a bundle by filesystem path (`--project /some/where`). Machine-specific; meaningless in a message to someone else.
- **M8** would address it by URL. Host-specific; dead when the host moves or the tunnel restarts.
- Two copies of one bundle are indistinguishable from two unrelated bundles. `[bundle].title` is a display string, `okf-base.yaml` names the linter's manifest, and the root `index.md` declares `okf_version` — **none of them identifies the bundle.**

**Why it matters, concretely.** The intended flow is "someone runs okrag over their corpus and exposes it when they want to". For a second person to reach it, they need something to put in `claude mcp add`. Today the only candidates are a path on a machine they do not have or a URL that is valid until it is not. There is no sentence of the form *point me at that corpus* that survives the corpus moving.

**What registration ergonomics do and do not fix.** `claude mcp add <name> -- okrag mcp --project <path>` is a better line for a human than editing JSON, and `AGENTS.md` should probably print it next to the portable entry. It is worth doing and it is not this item: it shortens the instruction, it does not make the target nameable, and it is one client's command, so it cannot be the only form offered.

**Three questions this splits into, and they have different urgencies.**

1. **Does a bundle get a stable id?** The expensive question, and the one to settle early, because it is a public identifier in the same class as the `okrag://` scheme (§13 M5) — reversing it breaks everyone who recorded one. If yes, it belongs in the bundle-root `index.md`, since that is the one file the format already gives frontmatter, and it must be *generated once and never rewritten*, unlike concept ids which churn on every rebuild by design.
2. **Can one server serve more than one bundle?** `--project` is one root per process. Hosted, `https://host/<bundle>` is the obvious shape and is not designed. Cheap to answer, but it depends on (1) for what `<bundle>` is.
3. **How does a consumer find out a bundle exists at all?** Out of scope for anything soon — that is a directory service, and it is a different product.

**On the hosted answer, since it is the obvious one.** The natural fix is a URL per bundle — `https://…/<owner>/<bundle>` — with a reverse proxy routing each to whatever serves it. The routing is well-understood and is the easy half. Three things are worth writing down before anyone builds it:

- **"A library or a web service" is a false choice, and this design already dissolved it.** Layer 2 is a directory and the sidecar rule keeps everything derived out of it, so one bundle can be a folder someone tars, a local stdio server, a Python import and a URL behind a proxy *at the same time*. Shipping a library forecloses nothing, which is why M5 precedes M8 rather than competing with it: you cannot host what is not installable and cannot be pointed at an explicit project.

- **A URL is a better location, not an identity.** It survives the consumer's machine, which a filesystem path does not. It does not survive the service, and it makes whoever runs the service the source of the name — so the same bundle then has two locations, its owner's disk and that URL, with nothing in the bundle asserting they are the same thing. Question (1) does not go away here; it takes more load.

- **The routing unit and question (2) are one decision seen from two sides.** `--project` is one root per process, so per-bundle routing implies one process per bundle: simple, blast-radius-isolated, and wasteful across many small bundles. One process serving several changes what `okrag mcp` is. Deciding it once, as *how a server addresses bundles*, is cheaper than deciding it twice — as routing and as CLI shape — and finding out later that the two disagree.

**The cheap version, which is probably the right first one.** A per-bundle URL does not require multi-tenancy. One owner, one machine, one DNS name they control, a tunnel — that is "live on the web" without becoming a service provider, and it is what "expose it when I want" means in practice. Multi-tenant routing is for when other people's corpora live on your hardware, which is a company rather than a feature: the proxy is a weekend, and M8's five criteria plus uptime, backups and someone else's private legal corpus on your disk are permanent. §12 Risk 7 stops being a design note at that point and becomes a question someone's lawyer asks.

**The trap to avoid.** Do not let (1) become an `access:`-style key or a registry the bundle depends on to be readable. The sidecar rule's logic applies: a bundle must stay something you can `tar` and `cat`. An id is a *fact about* the bundle written into it; a lookup a reader must perform to understand it is not.

**Gate.** M8 shipped without answering (1) or (2), so both are now due before anyone registers against a hosted bundle. (3) not scheduled, and possibly never here.

**Note on the intermittent case.** "Exposed when I feel like it" is usually a tunnel, and a tunnel URL is public the instant it exists — which means M6's authentication is not optional even for a bundle its owner thinks of as temporarily shared. That is worth stating because intermittent hosting *feels* lower-stakes than permanent hosting and is not.

---

## 13. Vectors at tens of thousands of concepts

The query side is already the right shape: `vectors/knn.py::nearest` is one `matrix @ query` over L2-normalized rows, which NumPy does in milliseconds even at 100k concepts. Two things around it are not, and both are invisible at today's 18 concepts.

1. **`okrag index` builds the full `n x n` similarity matrix.** `knn.py::similarities` is `matrix @ matrix.T`, kept whole for `top_k` and the ghost rule. At 100k concepts that is 100k² float32, about 40 GB. This is the real ceiling, not search. The fix is to compute it in row blocks and keep only each block's top-k, so memory is `block x n` instead of `n x n`.
2. **`search` reads `embeddings.npy` from disk on every call.** `agent/query.py::_by_embeddings` calls `load_store` each time, so at 100k x 768 float32 every query reads ~300 MB before the milliseconds of math. `np.load(..., mmap_mode="r")`, or holding the matrix for the life of the server process, removes it. Holding it means the server must notice when `okrag index` rewrites the file.

**Gate.** A corpus large enough to feel either one. Until then this is twenty lines of NumPy that work.

---

## 14. A concept body that arrives on one line

Found by M5's exit session. The concept `add_source` authored came back with its whole body on one line: `### Sujeto Activo` and the list items sat mid-line, joined by double spaces, so they render as text. No okrag code collapses newlines — `agent/write.py::_normalized` only converts `\r\n` — so the model returned it that way.

**Why it belongs here.** It is the same class as a footnote definition left mid-line, which `synth/base.py::anchor_footnote_defs` repairs offline. A repair is testable and a prompt rule is not.

**Why it is not obviously easy.** Breaking before a mid-line `#` or `* ` guesses at structure: a `#` inside prose or a `*` used for emphasis would be broken too. The repair has to be narrow enough to be right — a heading marker after a run of two spaces, say — or it is a new defect.

**Gate.** A second occurrence, or one on a build rather than on `add_source`, which says whether it is one model call misbehaving or a pattern.
