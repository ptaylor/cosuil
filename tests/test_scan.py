"""End-to-end scan tests over the generated fixture set."""

from __future__ import annotations

from pathlib import Path

from cosuil.config import DEFAULT_EXTENSIONS, ScanConfig, db_path
from cosuil.db import Database
from cosuil.scanner import Scanner


def _run_scan(fixtures: Path, kinds=("exact", "similar"), fresh: bool = False,
              exclude: tuple | None = None) -> dict:
    cfg = ScanConfig.from_toml(
        root=fixtures,
        kinds=kinds,
        phash_threshold=6,
        cnn_threshold=0.85,
        include_hidden=False,
        extensions=DEFAULT_EXTENSIONS,
        workers=1,
        thumb_size=256,
        fresh=fresh,
        exclude_dirs=exclude,
    )
    db = Database(db_path())
    scanner = Scanner(db, cfg)
    return scanner.run()


def _member_paths(db: Database, scan_id: int):
    out = {}
    for g in db.list_groups(scan_id, per_page=200)["groups"]:
        detail = db.get_group(g["id"])
        out[g["id"]] = {
            "kind": g["kind"],
            "paths": {Path(m["path"]).name for m in detail["members"]},
        }
    return out


def test_scan_finds_exact_duplicates(fixtures_dir, tmp_dirs):
    result = _run_scan(fixtures_dir)
    assert result["images_found"] >= 10
    db = Database(db_path())
    groups = _member_paths(db, result["scan_id"])
    # the byte-identical .jpg/.jpeg pair must share a group (exact or a similar
    # group that absorbed the exact one)
    dup_group = next(
        (g for g in groups.values() if {"base.jpg", "base-copy.jpeg"} <= g["paths"]),
        None,
    )
    assert dup_group is not None


def test_scan_finds_similar_images(fixtures_dir, tmp_dirs):
    result = _run_scan(fixtures_dir)
    db = Database(db_path())
    groups = _member_paths(db, result["scan_id"])
    base_group = next(
        (g for g in groups.values() if "base.jpg" in g["paths"]), None
    )
    assert base_group is not None
    # re-saved / resized / different-format versions cluster with the base image
    assert "base-recompressed.jpg" in base_group["paths"]
    assert {"base.png", "base.webp", "base.ppm"} & base_group["paths"]


def test_distinct_image_not_grouped(fixtures_dir, tmp_dirs):
    result = _run_scan(fixtures_dir)
    db = Database(db_path())
    groups = _member_paths(db, result["scan_id"])
    base_group = next(g for g in groups.values() if "base.jpg" in g["paths"])
    assert "other.jpg" not in base_group["paths"]
    # the distinct image shares a group with nobody
    assert not any("other.jpg" in g["paths"] and g["paths"] != {"other.jpg"} for g in groups.values())


def test_broken_file_logged_not_fatal(fixtures_dir, tmp_dirs):
    result = _run_scan(fixtures_dir)
    assert result["errors"] >= 1
    scan = Database(db_path()).get_scan(result["scan_id"])
    assert scan["status"] == "done"


def test_incremental_rescan_reuses_hashes(fixtures_dir, tmp_dirs):
    first = _run_scan(fixtures_dir)
    second = _run_scan(fixtures_dir, fresh=False)
    # second scan re-hashes nothing except the file with no reusable hash (broken.jpg)
    assert second["images_hashed"] == 1
    assert second["hashes_reused"] >= 10
    assert second["images_found"] == first["images_found"]
    assert second["exact_groups"] == first["exact_groups"]
    # reused hashes are persisted on the new scan rows too
    db = Database(db_path())
    phash_rows = db._query(
        "SELECT phash FROM images WHERE scan_id = ?", (second["scan_id"],)
    )
    assert sum(1 for r in phash_rows if r["phash"]) >= 10
    # but a fresh scan re-hashes everything
    third = _run_scan(fixtures_dir, fresh=True)
    assert third["images_hashed"] > 10
    assert third["hashes_reused"] == 0


def test_scan_empty_directory(tmp_path, tmp_dirs):
    result = _run_scan(tmp_path)
    assert result["images_found"] == 0
    assert Database(db_path()).get_scan(result["scan_id"])["status"] == "done"


def test_excluded_directory_not_scanned(fixtures_dir, tmp_dirs):
    import shutil

    (fixtures_dir / "Takeout").mkdir()
    shutil.copyfile(fixtures_dir / "base.jpg", fixtures_dir / "Takeout" / "base.jpg")
    shutil.copyfile(fixtures_dir / "other.jpg", fixtures_dir / "Takeout" / "other.jpg")

    result = _run_scan(fixtures_dir, exclude=("Takeout",))
    db = Database(db_path())
    rows = db.get_image_rows(result["scan_id"])
    assert all("Takeout" not in r["path"] for r in rows)
    assert result["images_found"] >= 10  # everything except the Takeout copies
