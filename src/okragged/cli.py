from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer

from . import config as config_module
from . import okf, vectors
from .agent import write_contract
from .api.app import WEB_DIST, create_app
from .config import Config
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

    _print_summary(summary, dry_run=dry_run)
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
    except MissingEmbedCredential as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(2) from None
    _print_index(summary)
    raise typer.Exit(1 if summary.embed.errors else 0)


@app.command()
def agent() -> None:
    """Regenerate the agent contract: agent/AGENTS.md, agent/mcp_server.py, .mcp.json."""
    cfg = config_module.load()
    if not cfg.bundle_root.exists():
        typer.secho(f"no bundle at {cfg.bundle_root}", fg="red")
        raise typer.Exit(2)
    for path in write_contract(cfg):
        typer.secho(f"  wrote {path.relative_to(cfg.project_root)}", fg="green")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8000, help="Port to bind."),
) -> None:
    """Serve the read-only API, and the graph view when web/dist is built."""
    cfg = _load()
    if not cfg.bundle_root.exists():
        typer.secho(f"no bundle at {cfg.bundle_root}", fg="red")
        raise typer.Exit(2)

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
    if not (cfg.project_root / WEB_DIST).is_dir():
        typer.secho(
            f"  no {WEB_DIST} - API only; `npm run build` in web/ to serve the view",
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


def _print_summary(summary: IngestSummary, dry_run: bool) -> None:
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
