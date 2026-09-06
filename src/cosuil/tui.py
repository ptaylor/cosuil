"""Rich-powered live terminal display for scans (Docker-style colored output)."""

from __future__ import annotations

import sys
from collections import deque
from typing import Optional

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.table import Table
from rich.text import Text

from .scanner import ScanCounters


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


class ScanTUI:
    """Collects progress and renders a live dashboard, then a final summary."""

    def __init__(self, root: str, quiet: bool = False, verbose: bool = False,
                 console: Optional[Console] = None):
        self.root = root
        self.quiet = quiet
        self.verbose = verbose
        self.console = console or Console()
        self.counters = ScanCounters()
        self.activity: deque[str] = deque(maxlen=12)
        self._live: Live | None = None
        self._hashing = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=36, style="magenta", complete_style="green"),
            TextColumn("{task.completed}/{task.total}"),
            console=self.console,
        )
        self._hash_task: TaskID | None = None

    # -- callbacks ------------------------------------------------------------
    def update(self, counters: ScanCounters) -> None:
        self.counters = counters
        if counters.images_found:
            if self._hash_task is None:
                self._hash_task = self._hashing.add_task(
                    "hashing", total=counters.images_found
                )
            self._hashing.update(
                self._hash_task,
                completed=min(counters.images_hashed, counters.images_found),
            )

    def log(self, line: str) -> None:
        self.activity.append(line)
        if self.verbose and not self.quiet:
            self.console.print(f"[dim]{line}[/dim]")

    # -- live context -----------------------------------------------------------
    def __enter__(self) -> "ScanTUI":
        if self.quiet:
            return self
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=10,
            transient=True,
        )
        self._live.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    # -- rendering ---------------------------------------------------------------
    def _stage_label(self) -> str:
        labels = {
            "starting": "starting",
            "discovering": "walking directory tree",
            "exact": "hashing bytes (exact duplicates)",
            "hashing": "computing perceptual hashes",
            "grouping": "grouping similar images",
            "deep": "CNN embeddings (visual similarity)",
            "done": "finished",
        }
        return labels.get(self.counters.stage, self.counters.stage)

    def _render(self):
        c = self.counters
        header = Text()
        header.append("cosúil", style="bold magenta")
        header.append("  scan ", style="dim")
        header.append(self.root, style="cyan")
        header.append(f"   [dim]{self._stage_label()}[/dim]")

        stats = Text()
        stats.append(f"files {c.files_walked}", style="bold cyan")
        stats.append("  ·  ", style="dim")
        stats.append(f"images {c.images_found}", style="bold cyan")
        stats.append("  ·  ", style="dim")
        stats.append(f"hashed {c.images_hashed}", style="bold green")
        stats.append("  ·  ", style="dim")
        stats.append(f"exact groups {c.exact_groups}", style="bold yellow")
        stats.append("  ·  ", style="dim")
        stats.append(f"similar groups {c.similar_groups}", style="bold yellow")
        if c.errors:
            stats.append("  ·  ", style="dim")
            stats.append(f"errors {c.errors}", style="bold red")
        stats.append("  ·  ", style="dim")
        stats.append(f"{c.elapsed:.1f}s", style="dim")

        body = [header, stats, Text(), self._hashing, Text()]
        if self.activity:
            lines = "\n".join(f"[dim]{line}[/dim]" for line in self.activity)
            body.append(Panel(lines, title="[bold]activity[/bold]",
                              border_style="blue", box=box.ROUNDED, padding=(0, 1)))
        return _vstack(body)

    # -- plain (non-TTY) output ----------------------------------------------------
    def plain(self, counters: ScanCounters) -> None:
        """One-line stage transitions for non-TTY output."""
        self.console.print(
            f"[cosuil] {self._stage_label_for(counters.stage)}: "
            f"files={counters.files_walked} images={counters.images_found} "
            f"hashed={counters.images_hashed} "
            f"exact={counters.exact_groups} similar={counters.similar_groups}"
        )

    def _stage_label_for(self, stage: str) -> str:
        saved = self.counters.stage
        self.counters.stage = stage
        label = self._stage_label()
        self.counters.stage = saved
        return label

    # -- final summary ---------------------------------------------------------------
    def show_summary(self, result: dict, groups: int = 0, reclaimable: int = 0) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        table = Table(title="scan summary", box=box.ROUNDED, border_style="magenta",
                      title_style="bold magenta", show_header=False)
        table.add_column("metric", style="bold cyan", no_wrap=True)
        table.add_column("value", style="bold white")
        table.add_row("files walked", f"{result['files_walked']:,}")
        table.add_row("images found", f"{result['images_found']:,}")
        table.add_row("images hashed", f"{result['images_hashed']:,}")
        table.add_row("exact duplicate groups", f"{result['exact_groups']:,}")
        table.add_row("similar groups", f"{result['similar_groups']:,}")
        table.add_row("deep (CNN) groups", f"{result['deep_groups']:,}")
        table.add_row("unreadable files", f"{result['errors']:,}")
        table.add_row("review groups", f"{groups:,}")
        table.add_row("reclaimable", f"{_fmt_bytes(reclaimable)}", style="bold green")
        table.add_row("elapsed", f"{result['elapsed']:.1f}s")
        self.console.print()
        self.console.print(table)
        if not self.quiet:
            self.console.print(
                "\n[dim]Run `cosuil serve` to review and select keepers in the browser.[/dim]"
            )


def _vstack(renderables):
    from rich.console import Group

    return Group(*renderables)
