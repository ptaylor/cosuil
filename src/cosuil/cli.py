"""Typer CLI: scan / serve / report."""

from __future__ import annotations

import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from .config import DEFAULT_EXTENSIONS, ScanConfig, config_dir, db_path, reports_dir, thumbs_dir
from .db import Database
from .scanner import Scanner
from .tui import ScanTUI

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="cosúil — find duplicate and similar photos, review, and trash the rest.")
console = Console()

KIND_HELP = "Which detection tiers to run: exact, similar, both, or all (adds CNN deep tier)."


def _open_db() -> Database:
    """Open the database, failing fast with a friendly message if it's busy."""
    try:
        return Database(db_path())
    except sqlite3.OperationalError as exc:
        console.print(
            f"[bold red]database unavailable:[/bold red] {exc} "
            f"(another cosúil process may be running a scan)"
        )
        raise typer.Exit(1) from exc


def _kinds_for(kind: str) -> tuple[str, ...]:
    if kind == "exact":
        return ("exact",)
    if kind == "similar":
        return ("similar",)
    if kind == "both":
        return ("exact", "similar")
    if kind == "all":
        return ("exact", "similar", "deep")
    raise typer.BadParameter(f"unknown kind '{kind}' (use exact|similar|both|all)")


@app.command()
def scan(
    root: Path = typer.Argument(..., help="Directory to scan recursively"),
    kind: str = typer.Option("both", "--kind", "-k", help=KIND_HELP),
    threshold: int = typer.Option(6, "--threshold", "-t",
                                  help="Perceptual hash Hamming distance cutoff (0-64)"),
    cnn_threshold: float = typer.Option(0.85, "--cnn-threshold",
                                        help="Cosine similarity cutoff for the CNN tier"),
    hidden: bool = typer.Option(False, "--hidden", help="Include hidden files/directories"),
    workers: int = typer.Option(0, "--workers", "-w", help="Parallel workers (0 = auto)"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Plain output, no live TUI"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Echo activity lines"),
    fresh: bool = typer.Option(False, "--fresh", help="Ignore hashes from previous scans"),
    exclude: Optional[list[str]] = typer.Option(
        None, "--exclude",
        help="Skip a directory: absolute path or bare directory name (repeatable)",
    ),
) -> None:
    """Scan ROOT for duplicate and similar images."""
    if not root.exists() or not root.is_dir():
        console.print(f"[bold red]error:[/bold red] {root} is not a directory")
        raise typer.Exit(1)
    if not 0 <= threshold <= 64:
        console.print("[bold red]error:[/bold red] --threshold must be between 0 and 64")
        raise typer.Exit(1)

    cfg = ScanConfig.from_toml(
        root=root,
        kinds=_kinds_for(kind),
        phash_threshold=threshold,
        cnn_threshold=cnn_threshold,
        include_hidden=hidden,
        extensions=DEFAULT_EXTENSIONS,
        workers=workers,
        thumb_size=256,
        fresh=fresh,
        exclude_dirs=tuple(exclude) if exclude else None,
    )
    db = _open_db()
    tui = ScanTUI(str(root), quiet=quiet, verbose=verbose, console=console)

    is_tty = sys.stdout.isatty()
    plain_last_stage = None

    def on_progress(counters):
        nonlocal plain_last_stage
        if quiet or not is_tty:
            if counters.stage != plain_last_stage:
                plain_last_stage = counters.stage
                tui.plain(counters)
            return
        tui.update(counters)

    scanner = Scanner(db, cfg, on_progress=on_progress, on_log=tui.log)
    try:
        with tui:
            result = scanner.run()
    except Exception as exc:
        console.print(f"[bold red]scan failed:[/bold red] {exc}")
        raise typer.Exit(1)

    scan = db.get_scan(result["scan_id"]) or {}
    groups = _count_groups(db, result["scan_id"])
    reclaimable = _reclaimable(db, result["scan_id"])
    tui.show_summary(result, groups=groups, reclaimable=reclaimable)


@app.command()
def serve(
    port: int = typer.Option(8787, "--port", "-p", help="Port to bind"),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the browser"),
) -> None:
    """Start the review web UI and open it in the browser."""
    import uvicorn

    from .server.app import create_app

    url = f"http://127.0.0.1:{port}"
    if not no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    console.print(f"[bold magenta]cosúil[/bold magenta] review UI → [cyan]{url}[/cyan]")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def report(
    root: Optional[Path] = typer.Argument(None, help="Show the latest scan of this directory"),
    scan_id: Optional[int] = typer.Option(None, "--scan", help="Show a specific scan id"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of groups to list"),
) -> None:
    """Show scan summaries and the top duplicate groups."""
    db = _open_db()
    if scan_id is not None:
        scan = db.get_scan(scan_id)
    elif root is not None:
        scan = db.latest_scan(str(root))
    else:
        scan = db.latest_scan()
    if scan is None:
        console.print("[yellow]no scans found yet[/yellow] — run `cosuil scan DIR` first")
        raise typer.Exit(0)

    table = Table(title=f"scan {scan['id']} — {scan['root']}",
                  box=box.ROUNDED, border_style="magenta", title_style="bold magenta")
    for col in ("kind", "count", "discard", "reclaimable", "status"):
        table.add_column(col)
    listing = db.list_groups(scan["id"], sort="bytes", page=1, per_page=limit)
    for g in listing["groups"]:
        table.add_row(
            g["kind"],
            str(g["member_count"]),
            str(g["discard_count"]),
            _fmt_bytes(g["reclaimable_bytes"]),
            g["status"],
        )
    remaining = listing["total"] - min(limit, listing["total"])
    if remaining > 0:
        table.add_row(f"[dim]{remaining} more…[/dim]", "", "", "", "")
    console.print(table)


@app.command()
def paths() -> None:
    """Show where cosuil stores its data on this machine."""
    table = Table(title="cosuil storage locations", box=box.ROUNDED,
                  border_style="magenta", title_style="bold magenta")
    table.add_column("what", style="bold cyan")
    table.add_column("path")
    table.add_row("config file", str(config_dir() / "config.toml"))
    table.add_row("scan database", str(db_path()))
    table.add_row("apply reports", str(reports_dir()))
    table.add_row("thumbnails", str(thumbs_dir()))
    console.print(table)


@app.command()
def scans(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of scans to list"),
) -> None:
    """List previous scans (saved in the local database)."""
    db = _open_db()
    rows = db.list_scans(limit)
    if not rows:
        console.print("[yellow]no scans yet[/yellow] — run `cosuil scan DIR` first")
        return
    table = Table(title="scans", box=box.ROUNDED, border_style="magenta",
                  title_style="bold magenta")
    for col in ("id", "root", "status", "images", "groups", "started"):
        table.add_column(col)
    for s in rows:
        groups = s["exact_groups"] + s["similar_groups"] + s["deep_groups"]
        table.add_row(
            str(s["id"]), s["root"], s["status"], str(s["images_found"]),
            str(groups), s["started_at"],
        )
    console.print(table)


@app.command()
def delete_scan(
    scan_id: int = typer.Option(..., "--scan", help="Scan id to delete (see `cosuil scans`)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a saved scan and its review data (files on disk are never touched)."""
    db = _open_db()
    scan = db.get_scan(scan_id)
    if scan is None:
        console.print(f"[yellow]scan {scan_id} not found[/yellow]")
        raise typer.Exit(1)
    if scan["status"] == "running":
        console.print("[bold red]cannot delete a running scan[/bold red]")
        raise typer.Exit(1)
    if not yes:
        ok = typer.confirm(
            f"Delete scan {scan_id} ({scan['root']}, {scan['images_found']} images)?"
        )
        if not ok:
            raise typer.Exit(0)
    db.delete_scan(scan_id)
    console.print(f"[green]deleted scan {scan_id}[/green]")


def _count_groups(db: Database, scan_id: int) -> int:
    return db.list_groups(scan_id, per_page=1)["total"]


def _reclaimable(db: Database, scan_id: int) -> int:
    stats = db.decision_stats(scan_id)
    return stats.get("discard", {}).get("bytes", 0)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":
    main()
