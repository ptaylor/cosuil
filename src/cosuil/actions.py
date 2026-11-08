"""Applying review decisions: move non-kept files to the OS trash (reversible).

Safety rules, enforced here rather than trusted to the UI:

* A file is trashed only if it is *exactly* the file that was reviewed: a
  regular, non-symlink file inside the scan root whose size and mtime still
  match what the scan recorded.
* An image counts as discarded only if *every* group it belongs to says
  discard. A keep (or no decision) anywhere protects it.
* A group never loses its last copy: if every member that still exists on disk
  would be trashed, that group is skipped entirely and reported.
* Files already missing are never "cleaned up" from the database — an
  unmounted drive looks exactly like that.
* Trashed files are removed from the database straight away, so the review
  state matches the disk and a second apply is a harmless no-op.
"""

from __future__ import annotations

import json
import os
import stat
import time
from datetime import datetime
from pathlib import Path

from send2trash import send2trash

from .config import reports_dir
from .db import Database

_FLUSH_EVERY = 100


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives / mixed absolute+relative
        return False


def _inspect(item: dict, root_real: str) -> tuple[str, str]:
    """Classify a file: ('ok'|'missing'|'unsafe', reason)."""
    path = item["path"]
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing", "file is missing"
    except OSError as exc:
        return "unsafe", f"cannot read file: {exc.strerror or exc}"
    if stat.S_ISLNK(st.st_mode):
        return "unsafe", "is a symbolic link"
    if not stat.S_ISREG(st.st_mode):
        return "unsafe", "is not a regular file"
    if st.st_size != item["size"] or st.st_mtime_ns != item["mtime_ns"]:
        return "unsafe", "changed since the scan — rescan before trashing"
    if not _inside(os.path.realpath(os.path.dirname(path)), root_real):
        return "unsafe", "is outside the scanned directory"
    return "ok", ""


def plan_apply(db: Database, scan_id: int) -> dict:
    """Work out what an apply would do, without touching anything.

    Returns ``{"items": [...], "skipped": [...], "blocked_groups": [...]}``;
    each item has image_id/path/size/mtime_ns, each skip has path/reason.
    """
    scan = db.get_scan(scan_id)
    root_real = os.path.realpath(scan["root"]) if scan else ""
    rows = db.apply_candidates(scan_id)

    decisions: dict[int, set[str]] = {}
    images: dict[int, dict] = {}
    groups: dict[int, list[int]] = {}
    for r in rows:
        decisions.setdefault(r["image_id"], set()).add(r["decision"])
        images[r["image_id"]] = r
        groups.setdefault(r["group_id"], []).append(r["image_id"])

    skipped: list[dict] = []
    status: dict[int, tuple[str, str]] = {}

    def check(image_id: int) -> tuple[str, str]:
        if image_id not in status:
            status[image_id] = _inspect(images[image_id], root_real)
        return status[image_id]

    candidates: set[int] = set()
    for image_id, seen in decisions.items():
        if "discard" not in seen:
            continue
        item = images[image_id]
        if seen != {"discard"}:
            skipped.append({"path": item["path"],
                            "reason": "kept or undecided in another group"})
            continue
        kind, reason = check(image_id)
        if kind != "ok":
            skipped.append({"path": item["path"], "reason": reason})
            continue
        candidates.add(image_id)

    # Never remove the last surviving copy of a group. Protecting images only
    # ever adds survivors elsewhere, so a single pass is enough.
    blocked: list[int] = []
    protected: set[int] = set()
    for group_id, members in groups.items():
        doomed = [i for i in members if i in candidates]
        if not doomed:
            continue
        survivors = [i for i in members
                     if i not in candidates and check(i)[0] != "missing"]
        if not survivors:
            blocked.append(group_id)
            protected.update(doomed)
    for image_id in sorted(protected):
        skipped.append({"path": images[image_id]["path"],
                        "reason": "would leave its group with no copy"})
    candidates -= protected

    items = sorted((images[i] for i in candidates), key=lambda r: r["path"])
    return {
        "items": [{k: r[k] for k in ("image_id", "path", "size", "mtime_ns")}
                  for r in items],
        "skipped": sorted(skipped, key=lambda s: s["path"]),
        "blocked_groups": sorted(blocked),
    }


def apply_decisions(db: Database, scan_id: int, dry_run: bool = False) -> dict:
    """Trash every safely-discardable image in the scan.

    With *dry_run* nothing is changed and no report is written. Per-file
    problems are collected into the result rather than raised.
    """
    plan = plan_apply(db, scan_id)
    items, skipped = plan["items"], list(plan["skipped"])
    if dry_run:
        return {
            "scan_id": scan_id, "dry_run": True,
            "moved": len(items), "bytes": sum(i["size"] for i in items),
            "errors": [], "skipped": skipped,
            "blocked_groups": plan["blocked_groups"], "report_file": None,
        }

    scan = db.get_scan(scan_id)
    root_real = os.path.realpath(scan["root"])
    moved: list[str] = []
    moved_ids: list[int] = []
    bytes_moved = 0
    errors: list[dict] = []
    try:
        for item in items:
            path = item["path"]
            # Re-verify right before acting: the plan may be seconds old.
            kind, reason = _inspect(item, root_real)
            if kind != "ok":
                skipped.append({"path": path, "reason": reason})
                continue
            try:
                send2trash(path)
            except Exception as exc:
                errors.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})
                continue
            moved.append(path)
            moved_ids.append(item["image_id"])
            bytes_moved += item["size"]
            if len(moved_ids) % _FLUSH_EVERY == 0:
                db.remove_images(moved_ids[-_FLUSH_EVERY:])
    finally:
        # Runs on interrupt too, so the database never keeps trashed files.
        db.remove_images(moved_ids[len(moved_ids) // _FLUSH_EVERY * _FLUSH_EVERY:])
        db.prune_scan(scan_id)
        report_file = _write_report(scan_id, moved, bytes_moved, errors, skipped)

    return {
        "scan_id": scan_id, "dry_run": False,
        "moved": len(moved), "bytes": bytes_moved,
        "errors": errors, "skipped": skipped,
        "blocked_groups": plan["blocked_groups"],
        "report_file": str(report_file) if report_file else None,
    }


def _write_report(
    scan_id: int, moved: list[str], bytes_moved: int,
    errors: list[dict], skipped: list[dict],
) -> Path | None:
    """Best-effort JSON record of the run; never masks the run's own outcome."""
    try:
        reports_dir().mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = reports_dir() / f"apply-{scan_id}-{stamp}.json"
        payload = {
            "scan_id": scan_id,
            "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "moved_to_trash": moved,
            "bytes": bytes_moved,
            "errors": errors,
            "skipped": skipped,
            "note": "Files were moved to the OS trash (recoverable).",
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None
