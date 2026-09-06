"""SQLite persistence for scans, images, groups and decisions."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT,
    files_walked INTEGER NOT NULL DEFAULT 0,
    images_found INTEGER NOT NULL DEFAULT 0,
    images_hashed INTEGER NOT NULL DEFAULT 0,
    exact_groups INTEGER NOT NULL DEFAULT 0,
    similar_groups INTEGER NOT NULL DEFAULT 0,
    deep_groups INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    blake3 TEXT,
    phash TEXT,
    width INTEGER,
    height INTEGER,
    format TEXT,
    sharpness REAL,
    exif_json TEXT,
    quality_score REAL,
    quality_json TEXT,
    warning TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_images_scan ON images(scan_id);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    rep_image_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_groups_scan ON groups(scan_id);
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    decision TEXT NOT NULL DEFAULT 'undecided',
    PRIMARY KEY (group_id, image_id)
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        # migrations for databases created by older versions
        image_cols = {
            row[1] for row in self._conn.execute("PRAGMA table_info(images)")
        }
        if "warning" not in image_cols:
            self._conn.execute("ALTER TABLE images ADD COLUMN warning TEXT")
        # one scan record per directory: keep only the latest per root
        self._conn.execute(
            "DELETE FROM scans WHERE id NOT IN "
            "(SELECT MAX(id) FROM scans GROUP BY root)"
        )
        self._conn.commit()

    # -- low-level helpers -------------------------------------------------
    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- scans --------------------------------------------------------------
    def upsert_scan(self, root: str, config_json: dict) -> int:
        """One scan record per directory.

        Reuses the existing row for *root* (replacing its results) or creates
        a new one. Raises RuntimeError if a scan for the directory is running.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            row = self._conn.execute(
                "SELECT id, status FROM scans WHERE root = ?", (root,)
            ).fetchone()
            if row is None:
                cur = self._conn.execute(
                    "INSERT INTO scans (root, config_json, started_at) VALUES (?, ?, ?)",
                    (root, json.dumps(config_json), now),
                )
                self._conn.commit()
                return int(cur.lastrowid)
            if row["status"] == "running":
                raise RuntimeError("a scan for this directory is already running")
            scan_id = int(row["id"])
            # replace the previous results (groups cascade to their members)
            self._conn.execute("DELETE FROM groups WHERE scan_id = ?", (scan_id,))
            self._conn.execute("DELETE FROM images WHERE scan_id = ?", (scan_id,))
            self._conn.execute(
                "UPDATE scans SET config_json = ?, status = 'running', error = NULL, "
                "files_walked = 0, images_found = 0, images_hashed = 0, "
                "exact_groups = 0, similar_groups = 0, deep_groups = 0, "
                "started_at = ?, finished_at = NULL WHERE id = ?",
                (json.dumps(config_json), now, scan_id),
            )
            self._conn.commit()
            return scan_id

    def update_scan(self, scan_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._execute(
            f"UPDATE scans SET {cols} WHERE id = ?", (*fields.values(), scan_id)
        )

    def get_scan(self, scan_id: int) -> dict | None:
        rows = self._query("SELECT * FROM scans WHERE id = ?", (scan_id,))
        return dict(rows[0]) if rows else None

    def latest_scan(self, root: str | None = None) -> dict | None:
        if root:
            rows = self._query(
                "SELECT * FROM scans WHERE root = ? ORDER BY id DESC LIMIT 1", (root,)
            )
        else:
            rows = self._query("SELECT * FROM scans ORDER BY id DESC LIMIT 1")
        return dict(rows[0]) if rows else None

    def list_scans(self, limit: int = 20) -> list[dict]:
        rows = self._query(
            "SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def delete_scan(self, scan_id: int) -> None:
        """Delete a scan and (via FK cascade) its images, groups and decisions."""
        self._execute("DELETE FROM scans WHERE id = ?", (scan_id,))

    # -- images --------------------------------------------------------------
    def insert_image_files(self, scan_id: int, files) -> list[int]:
        rows = [(scan_id, f.path, f.size, f.mtime_ns) for f in files]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO images (scan_id, path, size, mtime_ns) VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return [
            int(r["id"])
            for r in self._query(
                "SELECT id FROM images WHERE scan_id = ? ORDER BY id", (scan_id,)
            )
        ]

    def get_image_rows(self, scan_id: int) -> list[sqlite3.Row]:
        return self._query(
            "SELECT id, path, size, mtime_ns, quality_score FROM images WHERE scan_id = ? ORDER BY id",
            (scan_id,),
        )

    def get_image(self, image_id: int) -> dict | None:
        rows = self._query("SELECT * FROM images WHERE id = ?", (image_id,))
        return dict(rows[0]) if rows else None

    def update_image(self, image_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._execute(
            f"UPDATE images SET {cols} WHERE id = ?", (*fields.values(), image_id)
        )

    def update_images_many(self, updates: Iterable[tuple[int, dict]]) -> None:
        """Batch-apply field dicts keyed by image id."""
        grouped: dict[str, list] = {}
        for image_id, fields in updates:
            for k, v in fields.items():
                grouped.setdefault(k, []).append((v, image_id))
        with self._lock:
            for k, pairs in grouped.items():
                self._conn.executemany(
                    f"UPDATE images SET {k} = ? WHERE id = ?", pairs
                )
            self._conn.commit()

    # -- hash reuse (incremental scans) ---------------------------------------
    def previous_hashes(self, root: str) -> dict[tuple, dict]:
        """{(path, size, mtime_ns): {'blake3': ..., 'phash': ...}} from the latest
        completed scan of *root* (the in-progress scan is excluded)."""
        rows = self._query(
            "SELECT * FROM scans WHERE root = ? AND status = 'done' "
            "ORDER BY id DESC LIMIT 1",
            (root,),
        )
        if not rows:
            return {}
        image_rows = self._query(
            "SELECT path, size, mtime_ns, blake3, phash, width, height, format, "
            "sharpness, exif_json, quality_score, quality_json FROM images WHERE scan_id = ?",
            (rows[0]["id"],),
        )
        meta = (
            "width", "height", "format", "sharpness", "exif_json",
            "quality_score", "quality_json",
        )
        return {
            (r["path"], r["size"], r["mtime_ns"]): {
                "blake3": r["blake3"],
                "phash": r["phash"],
                **{k: r[k] for k in meta},
            }
            for r in image_rows
        }

    # -- groups --------------------------------------------------------------
    def insert_group(self, scan_id: int, kind: str, image_ids: Sequence[int],
                     rep_image_id: int | None = None) -> int:
        cur = self._execute(
            "INSERT INTO groups (scan_id, kind, rep_image_id) VALUES (?, ?, ?)",
            (scan_id, kind, rep_image_id or image_ids[0]),
        )
        group_id = int(cur.lastrowid)
        self._executemany(
            "INSERT OR IGNORE INTO group_members (group_id, image_id) VALUES (?, ?)",
            [(group_id, iid) for iid in image_ids],
        )
        return group_id

    def get_group(self, group_id: int) -> dict | None:
        rows = self._query("SELECT * FROM groups WHERE id = ?", (group_id,))
        if not rows:
            return None
        group = dict(rows[0])
        members = self._query(
            """
            SELECT i.id AS image_id, i.path, i.size, i.mtime_ns, i.blake3, i.phash,
                   i.width, i.height, i.format, i.quality_score, i.quality_json,
                   i.exif_json, i.warning, i.error, m.decision
            FROM group_members m JOIN images i ON i.id = m.image_id
            WHERE m.group_id = ?
            ORDER BY COALESCE(i.quality_score, -1) DESC, i.id
            """,
            (group_id,),
        )
        group["members"] = [dict(r) for r in members]
        return group

    def list_groups(
        self,
        scan_id: int,
        kind: str | None = None,
        status: str | None = None,
        sort: str = "bytes",
        page: int = 1,
        per_page: int = 48,
    ) -> dict:
        where = ["g.scan_id = ?"]
        params: list[Any] = [scan_id]
        if kind:
            where.append("g.kind = ?")
            params.append(kind)
        if status:
            where.append("g.status = ?")
            params.append(status)
        cond = " AND ".join(where)
        order = {
            "bytes": "reclaimable_bytes DESC",
            "count": "member_count DESC",
            "quality": "max_quality DESC",
            "oldest": "g.id ASC",
        }.get(sort, "reclaimable_bytes DESC")
        total = self._query(
            f"SELECT COUNT(*) AS n FROM groups g WHERE {cond}", params
        )[0]["n"]
        rows = self._query(
            f"""
            SELECT g.id, g.kind, g.status, g.rep_image_id,
                   COUNT(m.image_id) AS member_count,
                   (SELECT COUNT(*) FROM group_members m2
                     WHERE m2.group_id = g.id AND m2.decision = 'discard') AS discard_count,
                   COALESCE(SUM(CASE WHEN m.decision = 'discard' THEN i.size ELSE 0 END), 0)
                     AS reclaimable_bytes,
                   MAX(i.quality_score) AS max_quality,
                   (SELECT MAX(m3.image_id) FROM group_members m3
                     JOIN images i3 ON i3.id = m3.image_id
                     WHERE m3.group_id = g.id AND i3.quality_score = MAX(i.quality_score)
                   ) AS best_image_id
            FROM groups g
            JOIN group_members m ON m.group_id = g.id
            JOIN images i ON i.id = m.image_id
            WHERE {cond}
            GROUP BY g.id
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (*params, per_page, (page - 1) * per_page),
        )
        return {"total": total, "page": page, "per_page": per_page,
                "groups": [dict(r) for r in rows]}

    # -- decisions -------------------------------------------------------------
    def set_decisions(self, group_id: int, decisions: dict[int, str]) -> None:
        valid = {"keep", "discard", "undecided"}
        member_ids = {
            int(r["image_id"])
            for r in self._query(
                "SELECT image_id FROM group_members WHERE group_id = ?", (group_id,)
            )
        }
        rows = []
        for image_id, decision in decisions.items():
            if int(image_id) not in member_ids or decision not in valid:
                continue
            rows.append((decision, group_id, int(image_id)))
        if rows:
            self._executemany(
                "UPDATE group_members SET decision = ? WHERE group_id = ? AND image_id = ?",
                rows,
            )

    def mark_reviewed(self, group_id: int) -> None:
        self._execute(
            "UPDATE groups SET status = 'reviewed', reviewed_at = ? WHERE id = ?",
            (time.strftime("%Y-%m-%dT%H:%M:%S"), group_id),
        )

    def mark_pending(self, group_id: int) -> None:
        self._execute(
            "UPDATE groups SET status = 'pending', reviewed_at = NULL WHERE id = ?",
            (group_id,),
        )

    def discard_list(self, scan_id: int) -> list[dict]:
        rows = self._query(
            """
            SELECT i.id AS image_id, i.path, i.size, g.id AS group_id
            FROM group_members m
            JOIN images i ON i.id = m.image_id
            JOIN groups g ON g.id = m.group_id
            WHERE g.scan_id = ? AND m.decision = 'discard'
            ORDER BY i.path
            """,
            (scan_id,),
        )
        return [dict(r) for r in rows]

    def decision_stats(self, scan_id: int) -> dict:
        rows = self._query(
            """
            SELECT m.decision, COUNT(*) AS n,
                   COALESCE(SUM(CASE WHEN m.decision = 'discard' THEN i.size ELSE 0 END), 0) AS bytes
            FROM group_members m
            JOIN images i ON i.id = m.image_id
            JOIN groups g ON g.id = m.group_id
            WHERE g.scan_id = ?
            GROUP BY m.decision
            """,
            (scan_id,),
        )
        return {r["decision"]: {"count": r["n"], "bytes": r["bytes"]} for r in rows}
