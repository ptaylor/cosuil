"""FastAPI app: start scans, browse groups, record decisions, apply trash moves."""

from __future__ import annotations

import json
import mimetypes
import os
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..actions import apply_decisions
from ..config import DEFAULT_EXTENSIONS, ScanConfig, db_path
from ..db import Database
from ..scanner import Scanner
from ..thumbs import get_preview, get_thumbnail

STATIC_DIR = Path(__file__).parent / "static"

_RUNNING: dict[int, threading.Thread] = {}


def _launch_scan(db: Database, cfg: ScanConfig) -> int:
    """Create a scan row and run it in a background thread."""
    scanner = Scanner(db, cfg)
    scan_id = db.create_scan(str(cfg.root), cfg.to_json())
    scanner.scan_id = scan_id

    def worker() -> None:
        try:
            scanner.run(scan_id=scan_id)
        except Exception as exc:  # pragma: no cover - defensive
            db.update_scan(scan_id, status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            _RUNNING.pop(scan_id, None)

    thread = threading.Thread(target=worker, daemon=True, name=f"scan-{scan_id}")
    _RUNNING[scan_id] = thread
    thread.start()
    return scan_id


class ScanRequest(BaseModel):
    root: str
    kinds: list[str] = ["exact", "similar"]
    phash_threshold: int = 6
    cnn_threshold: float = 0.85
    include_hidden: bool = False
    fresh: bool = False


class DecisionsRequest(BaseModel):
    decisions: dict[str, str]  # image_id -> keep|discard|undecided


def create_app(db: Optional[Database] = None) -> FastAPI:
    app = FastAPI(title="cosúil", version="0.1.0")
    state = {"db": db}

    def get_db() -> Database:
        if state["db"] is None:
            state["db"] = Database(db_path())
        return state["db"]

    # -- static frontend ------------------------------------------------------
    @app.get("/static/{name:path}")
    def static_file(name: str) -> FileResponse:
        root = STATIC_DIR.resolve()
        target = (root / name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # -- directory browser ------------------------------------------------------
    @app.get("/api/browse")
    def browse(path: str = Query("/")) -> dict:
        p = Path(path).expanduser()
        if not p.exists() or not p.is_dir():
            raise HTTPException(404, "directory not found")
        try:
            entries = sorted(p.iterdir(), key=lambda e: e.name.lower())
        except PermissionError:
            raise HTTPException(403, "permission denied")
        dirs = [
            {"name": e.name, "path": str(e)}
            for e in entries
            if e.is_dir() and not e.name.startswith(".")
        ]
        return {"path": str(p), "parent": str(p.parent), "dirs": dirs}

    # -- scans -------------------------------------------------------------------
    @app.get("/api/scans")
    def scans() -> dict:
        return {"scans": get_db().list_scans()}

    @app.get("/api/scans/{scan_id}")
    def scan_status(scan_id: int) -> dict:
        scan = get_db().get_scan(scan_id)
        if scan is None:
            raise HTTPException(404, "scan not found")
        stats = get_db().decision_stats(scan_id)
        scan["decisions"] = stats
        return scan

    @app.post("/api/scans")
    def start_scan(req: ScanRequest) -> dict:
        db = get_db()
        root = Path(req.root).expanduser()
        if not root.exists() or not root.is_dir():
            raise HTTPException(400, f"{req.root} is not a directory")
        kinds = tuple(k for k in req.kinds if k in ("exact", "similar", "deep")) or (
            "exact",
            "similar",
        )
        cfg = ScanConfig.from_toml(
            root=root,
            kinds=kinds,
            phash_threshold=req.phash_threshold,
            cnn_threshold=req.cnn_threshold,
            include_hidden=req.include_hidden,
            extensions=DEFAULT_EXTENSIONS,
            workers=0,
            thumb_size=256,
            fresh=req.fresh,
        )
        return {"scan_id": _launch_scan(db, cfg)}

    @app.post("/api/scans/{scan_id}/rescan")
    def rescan(scan_id: int) -> dict:
        """Rescan a previous scan's root with its saved settings.

        Incremental: unchanged files reuse their hashes, so only new/changed
        files are re-hashed.
        """
        db = get_db()
        prev = db.get_scan(scan_id)
        if prev is None:
            raise HTTPException(404, "scan not found")
        try:
            saved = json.loads(prev["config_json"] or "{}")
        except ValueError:
            saved = {}
        root = Path(prev["root"])
        if not root.exists() or not root.is_dir():
            raise HTTPException(400, f"{root} no longer exists")
        kinds = tuple(
            k for k in saved.get("kinds", []) if k in ("exact", "similar", "deep")
        ) or ("exact", "similar")
        cfg = ScanConfig.from_toml(
            root=root,
            kinds=kinds,
            phash_threshold=int(saved.get("phash_threshold", 6)),
            cnn_threshold=float(saved.get("cnn_threshold", 0.85)),
            include_hidden=bool(saved.get("include_hidden", False)),
            extensions=DEFAULT_EXTENSIONS,
            workers=0,
            thumb_size=int(saved.get("thumb_size", 256)),
            fresh=False,
        )
        return {"scan_id": _launch_scan(db, cfg)}

    # -- groups -------------------------------------------------------------------
    @app.get("/api/scans/{scan_id}/groups")
    def groups(
        scan_id: int,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        sort: str = "bytes",
        page: int = 1,
        per_page: int = Query(48, le=200),
    ) -> dict:
        db = get_db()
        if db.get_scan(scan_id) is None:
            raise HTTPException(404, "scan not found")
        listing = db.list_groups(scan_id, kind=kind, status=status, sort=sort,
                                 page=page, per_page=per_page)
        for g in listing["groups"]:
            g["thumb_url"] = f"/api/images/{g['rep_image_id']}/thumb"
            g["name"] = _member_name(db, g["id"])
        return listing

    @app.get("/api/groups/{group_id}")
    def group_detail(group_id: int) -> dict:
        db = get_db()
        group = db.get_group(group_id)
        if group is None:
            raise HTTPException(404, "group not found")
        scan = db.get_scan(group["scan_id"]) or {}
        root = scan.get("root") or ""
        for m in group["members"]:
            full = Path(m["path"])
            m["name"] = full.name
            m["dirname"] = str(full.parent)
            m["rel_dir"] = _rel_dir(str(full.parent), root)
            m["thumb_url"] = f"/api/images/{m['image_id']}/thumb"
            m["file_url"] = f"/api/images/{m['image_id']}/file"
            m["exif"] = _parse_json(m.pop("exif_json", None))
            m["factors"] = _parse_json(m.pop("quality_json", None))
            m["mtime"] = _mtime(m["mtime_ns"])
        group["root"] = root
        return group

    # -- decisions ------------------------------------------------------------------
    @app.post("/api/groups/{group_id}/decisions")
    def decisions(group_id: int, req: DecisionsRequest) -> dict:
        db = get_db()
        if db.get_group(group_id) is None:
            raise HTTPException(404, "group not found")
        db.set_decisions(group_id, {int(k): v for k, v in req.decisions.items()})
        db.mark_reviewed(group_id)
        return {"ok": True}

    @app.post("/api/scans/{scan_id}/apply")
    def apply(scan_id: int) -> dict:
        db = get_db()
        if db.get_scan(scan_id) is None:
            raise HTTPException(404, "scan not found")
        return apply_decisions(db, scan_id)

    # -- image serving -----------------------------------------------------------------
    @app.get("/api/images/{image_id}/thumb")
    def thumb(image_id: int) -> FileResponse:
        db = get_db()
        img = db.get_image(image_id)
        if img is None:
            raise HTTPException(404, "image not found")
        path = get_thumbnail(img["path"], img["mtime_ns"], img["size"])
        if path is None:
            raise HTTPException(404, "thumbnail unavailable")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/images/{image_id}/file")
    def file(image_id: int) -> FileResponse:
        db = get_db()
        img = db.get_image(image_id)
        if img is None:
            raise HTTPException(404, "image not found")
        preview = get_preview(img["path"], img["mtime_ns"], img["size"], img["format"])
        if preview is None:
            raise HTTPException(404, "preview unavailable")
        media = mimetypes.guess_type(preview.name)[0] or "application/octet-stream"
        return FileResponse(preview, media_type=media)

    return app


def _member_name(db: Database, group_id: int) -> str:
    group = db.get_group(group_id)
    if not group or not group["members"]:
        return ""
    return Path(group["members"][0]["path"]).name


def _rel_dir(dirname: str, root: str) -> str:
    """Directory relative to the scan root when inside it, else absolute."""
    try:
        rel = os.path.relpath(dirname, root)
    except ValueError:
        return dirname
    if rel == ".":
        return "."
    if rel.startswith(".."):
        return dirname
    return rel


def _parse_json(value: Optional[str]) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}


def _mtime(ns: Optional[int]) -> Optional[float]:
    if ns is None:
        return None
    return ns / 1e9
