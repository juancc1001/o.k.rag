from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from . import config as config_module
from . import okf, vectors
from .agent import is_registered, write_contract
from .agent import register as agent_register
from .api.app import STATIC_DIR, create_app
from .config import Config
from .lock import LockHeld
from .pipeline import IngestSummary, ingest, inspect_extraction
from .synth import MissingCredential, resolve
from .vectors import MissingEmbedCredential

app = typer.Typer(add_completion=False, help="Build an OKF v0.2 bundle from a folder of files.")


def _force_utf8_output() -> None:
    """stdout/stderr default to the locale codepage, not UTF-8.

    On Windows that is cp1252, so redirecting output raises UnicodeEncodeError
    the moment a concept title contains a character outside it (an em dash, a
    math symbol, a curly quote). Accented Latin text happens to fit in cp1252,
    which makes the failure look intermittent.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


@app.callback()
def _main() -> None:
    _force_utf8_output()


def _web_root() -> Path:
    """`web/`, resolved relative to this checkout - absent on an installed wheel,
    which ships `api/static/` prebuilt instead."""
    return Path(__file__).resolve().parents[2] / "web"


def _view_is_stale(web_root: Path, static_dir: Path = STATIC_DIR) -> bool:
    """Built output missing, or older than anything that produces it."""
    index_html = static_dir / "index.html"
    if not index_html.is_file():
        return True
    watched = [web_root / "index.html", web_root / "vite.config.ts", web_root / "package.json"]
    src = web_root / "src"
    if src.is_dir():
        watched.extend(p for p in src.rglob("*") if p.is_file())
    newest = max((p.stat().st_mtime for p in watched if p.is_file()), default=0.0)
    return newest > index_html.stat().st_mtime


def _build_view(web_root: Path) -> str | None:
    """`npm install` (if `node_modules` is missing) then `npm run build`.

    Returns an error message on failure, `None` on success. Follows the same
    subprocess-encoding pattern as `okf/validate.py::_run_okflint` - never let
    a child inherit the locale codepage.
    """
    npm = shutil.which("npm")
    if npm is None:
        return "npm not found on PATH - install Node 20+, or run it yourself in web/"
    if not (web_root / "node_modules").is_dir():
        proc = subprocess.run(
            [npm, "install"],
            cwd=web_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            # the child's own stdout is a pipe, so its Python picks the locale
            # codepage on its own unless told otherwise (see `_run_okflint`)
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if proc.returncode != 0:
            return f"npm install failed:\n{proc.stdout}{proc.stderr}"
    proc = subprocess.run(
        [npm, "run", "build"],
        cwd=web_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if proc.returncode != 0:
        return f"npm run build failed:\n{proc.stdout}{proc.stderr}"
    return None


def _load() -> Config:
    cfg = config_module.load()
    from dotenv import load_dotenv

    # utf-8-sig: a BOM (Notepad, VS Code, PowerShell `Set-Content -Encoding utf8`)
    # otherwise turns the first key into "﻿GEMINI_API_KEY" and fails silently
    load_dotenv(cfg.project_root / ".env", encoding="utf-8-sig")
    return cfg


@app.command()
def build(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Extract and report only; no model calls, no writes."
    ),
) -> None:
    """Discover sources, synthesize concepts, emit the bundle, validate it."""
    cfg = _load()
    if not cfg.sources_root.exists():
        typer.secho(f"no sources directory at {cfg.sources_root}", fg="red")
        raise typer.Exit(2)

    try:
        summary = ingest(cfg, dry_run=dry_run)
    except MissingCredential as exc:
        typer.secho(str(exc), fg="red")
        typer.secho("  copy .env.example to .env and add your key, or run `okragged doctor`", fg="bright_black")
        raise typer.Exit(2) from None
    except LockHeld as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(2) from None

    _print_summary(summary, dry_run=dry_run)
    if not dry_run and cfg.bundle_root.exists() and not is_registered(cfg):
        # mentioned, not done: registering edits the machine's MCP config, which a
        # build has no business writing (see agent/contract.py)
        typer.secho(
            "\nthe MCP server is not registered - `okragged agent --register` adds it "
            "to .mcp.json",
            fg="bright_black",
        )
    raise typer.Exit(_exit_code(summary))


@app.command()
def check() -> None:
    """Validate the existing bundle without rebuilding it."""
    cfg = config_module.load()
    if not cfg.bundle_root.exists():
        typer.secho(f"no bundle at {cfg.bundle_root}", fg="red")
        raise typer.Exit(2)
    report = okf.validate(cfg.bundle_root)
    _print_report(report)
    raise typer.Exit(0 if report.ok else 1)


@app.command()
def index() -> None:
    """Embed the bundle, project it to 2D, write graph.json. Sidecar only."""
    cfg = _load()
    if not cfg.bundle_root.exists():
        typer.secho(f"no bundle at {cfg.bundle_root}", fg="red")
        raise typer.Exit(2)
    try:
        summary = vectors.index(cfg)
    except (MissingEmbedCredential, LockHeld) as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(2) from None
    _print_index(summary)
    raise typer.Exit(1 if summary.embed.errors else 0)


@app.command()
def agent(
    register: bool = typer.Option(
        False,
        "--register",
        help="Also register the MCP server in .mcp.json. Edits a file okragged does not own.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Register the entry with --write, so that server offers add_source.",
    ),
    spend: bool = typer.Option(
        False,
        "--spend",
        help="Register the entry with --spend, so that server may use your credential.",
    ),
) -> None:
    """Regenerate the agent contract: agent/AGENTS.md. --register also writes .mcp.json."""
    cfg = config_module.load()
    if not cfg.bundle_root.exists():
        typer.secho(f"no bundle at {cfg.bundle_root}", fg="red")
        raise typer.Exit(2)
    # the two describe the entry `--register` writes, so on their own they describe
    # nothing: silently ignoring a switch somebody typed is worse than refusing it
    if (write or spend) and not register:
        typer.secho(
            "--write and --spend describe the entry --register writes; pass --register too",
            fg="red",
        )
        raise typer.Exit(2)
    written = write_contract(cfg)
    # asked for, never assumed: .mcp.json is the machine's MCP config, shared
    # with every other server registered there. The switches land only when
    # asked for in the same breath -- without them the entry stays bare and read-only
    if register:
        written.append(agent_register(cfg, write=write, spend=spend))
    for path in written:
        typer.secho(f"  wrote {path.relative_to(cfg.project_root)}", fg="green")
    if register:
        # said either way: the switches narrow what the same bare entry means,
        # so an operator registering for their own use needs to see what they
        # got, not just what they typed
        typer.secho(f"  the entry grants: {_granted(write, spend)}", fg="bright_black")
        if not (write and spend):
            typer.secho(
                "  add --write for add_source, and --spend to author concepts and "
                "embed queries on your key",
                fg="bright_black",
            )
    if not register and not is_registered(cfg):
        typer.secho(
            "  the MCP server is not registered - `okragged agent --register` adds it",
            fg="bright_black",
        )


@app.command()
def mcp(
    project: Path | None = typer.Option(
        None,
        "--project",
        envvar="OKRAGGED_PROJECT",
        help="Project root holding the bundle. Defaults to the current directory.",
    ),
    # no `envvar=` on either, deliberately, where --project has one: an ambient
    # variable that switches on writing or spending is the thing being fixed. An
    # instance may only be what the operator who wrote its .mcp.json asked for.
    write: bool = typer.Option(
        False,
        "--write",
        help="Offer the tool that records a source. Off by default: read-only.",
    ),
    spend: bool = typer.Option(
        False,
        "--spend",
        help="Read this project's .env. Search may then embed a query on your key, "
        "and with --write the recorded source is synthesized on it too.",
    ),
) -> None:
    """Serve this bundle to an MCP client over stdio. What .mcp.json registers."""
    # the target is explicit because it has to be: a server that inferred its
    # bundle from __file__ could not survive being pip-installed, and the
    # consumer this exists for is spawning it from a different tree entirely
    root = (project or Path.cwd()).resolve()
    cfg = config_module.load(root)
    # stderr, not stdout: the protocol is newline-delimited JSON on stdout, and
    # a diagnostic there corrupts the first frame of the session
    if not cfg.bundle_root.exists():
        typer.secho(f"no bundle at {cfg.bundle_root}", fg="red", err=True)
        raise typer.Exit(2)

    from .agent.server import main as serve_mcp

    try:
        serve_mcp(root, write=write, spend=spend)
    except RuntimeError as exc:  # the MCP SDK is an optional extra
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from None


@app.command("build-view")
def build_view() -> None:
    """Build the graph view from web/. Checkout-only - a wheel ships it prebuilt."""
    web_root = _web_root()
    if not web_root.is_dir():
        typer.secho(
            "no web/ here - this is an installed wheel, which ships the view prebuilt",
            fg="bright_black",
        )
        raise typer.Exit(0)
    typer.secho(f"building the view from {web_root}...", fg="bright_black")
    error = _build_view(web_root)
    if error:
        typer.secho(error, fg="red")
        raise typer.Exit(1)
    typer.secho(f"  wrote {STATIC_DIR}", fg="green")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8000, help="Port to bind."),
    all_: bool = typer.Option(
        False, "--all", help="Build the view first if it is missing or stale, then serve."
    ),
) -> None:
    """Serve the read-only API, and the graph view when it has been built."""
    cfg = _load()
    if not cfg.bundle_root.exists():
        typer.secho(f"no bundle at {cfg.bundle_root}", fg="red")
        raise typer.Exit(2)

    if all_:
        web_root = _web_root()
        if web_root.is_dir() and _view_is_stale(web_root):
            typer.secho(f"building the view from {web_root}...", fg="bright_black")
            error = _build_view(web_root)
            if error:
                typer.secho(error, fg="red")

    try:
        application = create_app(cfg)
    except RuntimeError as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(2) from None

    if not (cfg.sidecar_root / vectors.GRAPH_NAME).exists():
        typer.secho(
            f"  no {vectors.GRAPH_NAME} - /api/graph will 404 until `okragged index` runs",
            fg="yellow",
        )
    if not STATIC_DIR.is_dir():
        typer.secho(
            "  no built view - API only; `npm run build` in web/ to serve it",
            fg="bright_black",
        )

    import uvicorn

    typer.secho(f"  http://{host}:{port}/api/docs", fg="green")
    uvicorn.run(application, host=host, port=port, log_level="warning")


@app.command()
def extract(path: Path = typer.Argument(..., help="File to extract, for debugging.")) -> None:
    """Print the extracted text for one file. Runs offline - no model call."""
    cfg = config_module.load()
    if not path.exists():
        typer.secho(f"no such file: {path}", fg="red")
        raise typer.Exit(2)
    typer.echo(inspect_extraction(cfg, path))


@app.command()
def doctor() -> None:
    """Report what is configured and whether the model provider is reachable."""
    cfg = _load()
    provider = cfg.synth.provider

    typer.secho("config", bold=True)
    typer.echo(f"  project        {cfg.project_root}")
    typer.echo(f"  provider       {provider}")
    typer.echo(f"  effort         {cfg.synth.effort}  (anthropic only)")
    typer.echo(f"  max_tokens     {cfg.synth.max_tokens}")

    typer.secho("\npaths", bold=True)
    _report_path(".env", cfg.project_root / ".env", hint="copy .env.example")
    _report_path("sources/", cfg.sources_root, hint="create it and drop files in")
    _report_path("bundle/", cfg.bundle_root, hint="produced by `okragged build`")
    _report_path(okf.MANIFEST_NAME, cfg.project_root / okf.MANIFEST_NAME, hint="generated by `okragged build`")

    typer.secho("\ntools", bold=True)
    if shutil.which("okflint"):
        typer.secho("  okflint        found", fg="green")
    else:
        typer.secho("  okflint        missing - only the built-in checks will run", fg="yellow")

    # before the synth provider, which exits on failure: `index` has its own
    # provider and its own credential, and a missing key for one says nothing
    # about the other
    typer.secho(f"\nembeddings: {cfg.vectors.provider}  (`okragged index`)", bold=True)
    try:
        embed_impl = vectors.resolve(cfg.vectors.provider)
    except ValueError as exc:
        typer.secho(f"  {exc}", fg="red")
    else:
        embed_model = cfg.vectors.model or embed_impl.DEFAULT_MODEL
        typer.echo(f"  model          {embed_model}{'  (default)' if not cfg.vectors.model else ''}")
        typer.echo(
            f"  dim/k/tau      {cfg.vectors.dim} / {cfg.vectors.knn_k} / {cfg.vectors.ghost_threshold}"
        )
        if not embed_impl.ENV_VARS:
            typer.secho("  credential     not needed - runs locally", fg="green")
        elif embed_impl.credential_present():
            typer.secho("  credential     present", fg="green")
        else:
            typer.secho(
                f"  credential     missing ({' or '.join(embed_impl.ENV_VARS)})"
                " - `okragged index` will not run",
                fg="yellow",
            )
        _report_path(vectors.GRAPH_NAME, cfg.sidecar_root / vectors.GRAPH_NAME, hint="run `okragged index`")

    typer.secho(f"\nprovider: {provider}", bold=True)
    try:
        impl = resolve(provider)
    except ValueError as exc:
        typer.secho(f"  {exc}", fg="red")
        raise typer.Exit(2) from None

    model = cfg.synth.model or impl.DEFAULT_MODEL
    typer.echo(f"  model          {model}{'  (default)' if not cfg.synth.model else ''}")

    if not impl.credential_present():
        typer.secho(f"  credential     missing ({' or '.join(impl.ENV_VARS)})", fg="red")
        raise typer.Exit(1)
    typer.secho("  credential     present", fg="green")

    try:
        models = impl.list_models()
    except Exception as exc:  # network, auth, quota - report, don't traceback
        typer.secho(f"  reachable      NO - {type(exc).__name__}: {exc}", fg="red")
        raise typer.Exit(1) from None

    typer.secho(f"  reachable      yes ({len(models)} models)", fg="green")
    if model in models:
        typer.secho(f"  {model!r} available", fg="green")
    else:
        typer.secho(f"  {model!r} NOT in the list - set [synth].model to one of:", fg="red")
        for name in models[:25]:
            typer.echo(f"      {name}")
        if len(models) > 25:
            typer.echo(f"      ... and {len(models) - 25} more")
        raise typer.Exit(1)


def _report_path(label: str, path: Path, hint: str) -> None:
    if path.exists():
        typer.secho(f"  {label:<14} found", fg="green")
    else:
        typer.secho(f"  {label:<14} missing - {hint}", fg="yellow")


def _granted(write: bool, spend: bool) -> str:
    """What the entry just written permits, in the caller's own terms.

    Spelled out rather than echoed back as flags: the operator knows which
    switches they typed and not what each one turns on.
    """
    if write and spend:
        return "add_source, which authors and embeds the concept on your credential"
    if write:
        return "add_source, which records prose for the next `okragged build` to author"
    if spend:
        return "reads only, with search embedding the query on your credential"
    return "reads only - no add_source, and search ranks by index traversal"


def _print_summary(summary: IngestSummary, dry_run: bool) -> None:
    # first: what an earlier run left behind explains anything else that looks odd
    for note in summary.recovered:
        typer.secho(f"\n{note}", fg="yellow")

    verb = "would write" if dry_run else "wrote"
    total = sum(o.concept_count for o in summary.synthesized)
    noun = f"{total} concept(s) from {len(summary.synthesized)} source(s)"
    typer.secho(f"\n{verb} {noun}", fg="green")
    for outcome in summary.written:
        tail = "  (unchanged)" if outcome.skipped else ""
        typer.echo(f"  {outcome.concept_id:<40} <- {outcome.uri}{tail}")
        if outcome.skipped:
            continue
        for child_id in outcome.child_ids:
            typer.echo(f"    {child_id}")
        for dropped in outcome.dropped_sections:
            typer.secho(f"    dropped section (marker not found): {dropped}", fg="yellow")
        for failure in outcome.section_errors:
            typer.secho(f"    FAILED section {failure}", fg="red")
        for concept_id in outcome.removed:
            typer.secho(f"    removed stale {concept_id}", fg="bright_black")
        for failure in outcome.weave_errors:
            typer.secho(f"    FAILED to weave links into {failure}", fg="yellow")

    for outcome in summary.failed:
        typer.secho(f"  FAILED {outcome.uri}: {outcome.error}", fg="red")

    linked = sum(len(o.linked) for o in summary.outcomes)
    if linked:
        typer.secho(f"\nwove cross-links into {linked} concept(s)", fg="green")

    if summary.skipped:
        typer.secho(
            f"\nskipped {len(summary.skipped)} unchanged source(s) - no model call",
            fg="bright_black",
        )

    protected = {cid: why for o in summary.outcomes for cid, why in o.protected.items()}
    if protected:
        typer.secho("\nnot overwritten - a human owns these:", fg="yellow")
        for concept_id, why in protected.items():
            typer.echo(f"  {concept_id}: {why}")
        typer.secho("  the proposed replacements are in .okragged/proposed/", fg="bright_black")

    if summary.deprecated:
        typer.secho(
            f"\nsource gone - marked deprecated: {', '.join(summary.deprecated)}", fg="yellow"
        )

    if summary.invented_links:
        unique = sorted(set(summary.invented_links))
        typer.secho(
            f"\nlink target(s) that are not concepts, unwrapped to plain text: "
            f"{', '.join(unique)}",
            fg="yellow",
        )

    new_types = sorted({o.new_type for o in summary.outcomes if o.new_type})
    if new_types:
        typer.secho(
            f"\nnew type(s) proposed, not in okragged.toml [types].allowed: {', '.join(new_types)}",
            fg="yellow",
        )

    if summary.input_tokens or summary.output_tokens:
        typer.echo(
            f"\ntokens  in={summary.input_tokens}  out={summary.output_tokens}"
            f"  cache_read={summary.cache_read_tokens}"
        )

    if summary.report:
        _print_report(summary.report)


def _print_index(summary: vectors.IndexSummary) -> None:
    embedded = summary.embed
    typer.secho(
        f"\nembedded {len(embedded.embedded)} concept(s) with "
        f"{embedded.provider}/{embedded.model} at {embedded.dim} dims",
        fg="green",
    )
    if embedded.reused:
        typer.secho(
            f"  reused {len(embedded.reused)} unchanged vector(s) - no call",
            fg="bright_black",
        )
    if embedded.dropped:
        typer.secho(f"  dropped {len(embedded.dropped)} vector(s) with no concept", fg="bright_black")
    for concept_id, why in embedded.errors.items():
        typer.secho(f"  FAILED {concept_id}: {why}", fg="red")

    stats = summary.stats
    typer.echo(f"\nprojection     {summary.method}")
    typer.echo(
        f"graph          {stats.get('concepts', 0)} node(s), "
        f"{stats.get('links', 0)} link(s), {stats.get('ghosts', 0)} ghost(s), "
        f"{stats.get('broken_links', 0)} broken link(s)"
    )
    for path in summary.written:
        typer.secho(f"  {path}", fg="bright_black")


def _print_report(report: okf.Report) -> None:
    typer.echo("")
    if report.ok:
        typer.secho(
            f"OKF v0.2 conformant: {report.concepts} concept(s), 0 errors", fg="green"
        )
    elif report.errors:
        typer.secho(f"NOT conformant: {len(report.errors)} error(s)", fg="red")
    else:
        # don't print "0 error(s)" when okflint is the one objecting
        typer.secho(
            f"NOT conformant: built-in checks passed, okflint exited {report.okflint_exit}",
            fg="red",
        )
    for finding in report.errors:
        typer.secho(f"  {finding}", fg="red")
    for finding in report.warnings:
        typer.secho(f"  {finding}", fg="yellow")
    if report.okflint_note:
        typer.secho(f"  note: {report.okflint_note}", fg="bright_black")
    elif report.okflint_exit is not None:
        colour = "green" if report.okflint_exit == 0 else "red"
        typer.secho(f"  okflint: exit {report.okflint_exit}", fg=colour)
        if report.okflint_output:
            typer.echo(report.okflint_output)


def _exit_code(summary: IngestSummary) -> int:
    if summary.failed:
        return 1
    if summary.report and not summary.report.ok:
        return 1
    return 0


if __name__ == "__main__":
    app()
