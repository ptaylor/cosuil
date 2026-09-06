"""Applying review decisions: move non-kept files to the OS trash (reversible)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from send2trash import send2trash

from .config import reports_dir
from .db import Database


def apply_decisions(db: Database, scan_id: int) -> dict:
    """Trash every image with a 'discard' decision in the scan. Never raises."""
    items = db.discard_list(scan_id)
    moved, bytes_moved, errors = [], 0, []
    for item in items:
        path = item["path"]
        if not Path(path).exists():
            errors.append({"path": path, "error": "missing file"})
            continue
        try:
            send2trash(path)
            moved.append(path)
            bytes_moved += item["size"]
        except Exception as exc:
            errors.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})

    report_file = _write_report(scan_id, moved, bytes_moved, errors)
    return {
        "scan_id": scan_id,
        "moved": len(moved),
        "bytes": bytes_moved,
        "errors": errors,
        "report_file": str(report_file),
    }


def _write_report(
    scan_id: int, moved: list[str], bytes_moved: int, errors: list[dict]
) -> Path:
    reports_dir().mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = reports_dir() / f"apply-{scan_id}-{stamp}.json"
    payload = {
        "scan_id": scan_id,
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "moved_to_trash": moved,
        "bytes": bytes_moved,
        "errors": errors,
        "note": "Files were moved to the OS trash (recoverable).",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
