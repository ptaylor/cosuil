"""Database migration tests."""

from __future__ import annotations

import sqlite3

from cosuil.db import Database


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
    conn.commit()
    conn.close()

    Database(db_file)  # migration collapses duplicates to the latest

    conn = sqlite3.connect(db_file)
    count = conn.execute(
        "SELECT COUNT(*) FROM scans WHERE root = '/same/root'"
    ).fetchone()[0]
    conn.close()
    assert count == 1
