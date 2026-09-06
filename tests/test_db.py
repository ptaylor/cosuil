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
