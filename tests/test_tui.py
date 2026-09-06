"""Tests for the Rich TUI summary and quality scoring."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from cosuil.quality import composite_score, format_score
from cosuil.tui import ScanTUI


def test_summary_table_renders():
    console = Console(file=StringIO(), width=120, force_terminal=True)
    tui = ScanTUI("/tmp/somewhere", quiet=True, console=console)
    tui.show_summary(
        {
            "files_walked": 123,
            "images_found": 100,
            "images_hashed": 100,
            "exact_groups": 2,
            "similar_groups": 5,
            "deep_groups": 0,
            "errors": 1,
            "elapsed": 1.23,
        },
        groups=7,
        reclaimable=12_345_678,
    )
    out = console.file.getvalue()
    assert "scan summary" in out
    assert "similar groups" in out
    assert "reclaimable" in out
    assert "11.8 MiB" in out


def test_stage_labels():
    tui = ScanTUI("/tmp/x", quiet=True)
    tui.counters.stage = "hashing"
    assert "perceptual" in tui._stage_label()


def test_quality_scoring_prefers_lossless():
    assert format_score("PNG") == 1.0
    assert format_score("JPEG") < format_score("PNG")
    assert format_score("PPM") == 1.0
    assert format_score(None) > 0


def test_composite_score_bounds():
    total, factors = composite_score(
        width=4000, height=3000, size=5_000_000, fmt="JPEG",
        sharpness=900.0, exif={"camera": "X", "taken": "2024-01-01"},
    )
    assert 0.0 <= total <= 1.0
    assert set(factors) == {"resolution", "sharpness", "size", "format", "exif"}
    assert factors["exif"] == 1.0
