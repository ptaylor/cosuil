"""Database migration tests."""

from __future__ import annotations

import sqlite3

from cosuil.db import Database
from cosuil.discovery import FileInfo


def test_warning_column_migrated_for_old_databases(tmp_path):
    db_file = tmp_path / "cosuil.db"
    conn = sqlite3.connect(db_file)
    # simulate a database created before the `warning` column existed
    conn.execute(
        "CREATE TABLE images (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "scan_id INTEGER, path TEXT, size INTEGER, mtime_ns INTEGER)"
    )
    conn.commit()
    conn.close()

    Database(db_file)  # runs schema creation + migration

    conn = sqlite3.connect(db_file)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
    conn.close()
    assert "warning" in cols


def test_duplicate_scans_collapsed_to_latest(tmp_path):
    db_file = tmp_path / "cosuil.db"
    db = Database(db_file)
    db.close()

    # simulate legacy duplicate records for one root
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO scans (root, config_json, started_at) VALUES (?, ?, ?)",
        ("/same/root", "{}", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO scans (root, config_json, started_at) VALUES (?, ?, ?)",
        ("/same/root", "{}", "2026-01-02T00:00:00"),
    )
    conn.execute("PRAGMA user_version = 1")  # simulate a not-yet-migrated database
    conn.commit()
    conn.close()

    Database(db_file)  # migration collapses duplicates to the latest

    conn = sqlite3.connect(db_file)
    count = conn.execute(
        "SELECT COUNT(*) FROM scans WHERE root = '/same/root'"
    ).fetchone()[0]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert count == 1
    assert version == 2

    # reopening does not rerun migrations
    Database(db_file).close()
    conn = sqlite3.connect(db_file)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()


def test_takeover_reclaims_stale_running_scan(tmp_path):
    db = Database(tmp_path / "cosuil.db")
    first = db.upsert_scan("/root", {})
    db.update_scan(first, status="running")

    # a plain upsert refuses while the record says running
    try:
        db.upsert_scan("/root", {})
    except RuntimeError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected RuntimeError")

    # takeover replaces the stale running record with the same id
    second = db.upsert_scan("/root", {"kinds": ["exact"]}, takeover=True)
    assert second == first
    assert db.get_scan(first)["status"] == "running"
    db.close()


def test_previous_hashes_reuse_from_interrupted_scan(tmp_path):
    db = Database(tmp_path / "cosuil.db")
    scan_id = db.upsert_scan("/root", {})
    db.insert_image_files(
        scan_id,
        [FileInfo("/root/a.jpg", 10, 1), FileInfo("/root/b.jpg", 10, 2)],
    )
    rows = db.get_image_rows(scan_id)
    db.update_image(rows[0]["id"], phash="abcd", blake3="beef")
    db.update_scan(scan_id, status="error", error="interrupted")

    reused = db.previous_hashes("/root")
    # hashes finished before the interrupt are reusable
    assert reused[("/root/a.jpg", 10, 1)]["phash"] == "abcd"
    # the not-yet-hashed row is present but contributes nothing
    assert ("/root/b.jpg", 10, 2) in reused
    assert reused[("/root/b.jpg", 10, 2)].get("phash") is None
    db.close()


def test_scan_errors_lists_paths(tmp_path):
    db = Database(tmp_path / "cosuil.db")
    scan_id = db.upsert_scan("/root", {})
    db.insert_image_files(
        scan_id,
        [FileInfo("/root/a.jpg", 1, 1), FileInfo("/root/b.jpg", 2, 2)],
    )
    rows = db.get_image_rows(scan_id)
    db.update_image(rows[0]["id"], error="boom")
    db.update_image(rows[1]["id"], warning="truncated")

    errors = db.scan_errors(scan_id)
    assert [e["path"] for e in errors] == ["/root/a.jpg"]
    assert errors[0]["error"] == "boom"
    assert db.scan_warning_count(scan_id) == 1
    db.close()
