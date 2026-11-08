"""Safety and correctness of trashing files and deleting scans."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import cosuil.actions as actions_mod
from cosuil.db import Database
from cosuil.server import app as app_mod
from cosuil.server.app import create_app


@pytest.fixture()
def env(tmp_dirs, fixtures_dir, monkeypatch):
    """A scanned copy of the fixtures plus a trash that really moves files."""
    trashed: list[str] = []
    trash_dir = tmp_dirs[0] / "fake-trash"
    trash_dir.mkdir(parents=True, exist_ok=True)

    def fake_trash(path: str):
        trashed.append(path)
        shutil.move(path, str(trash_dir / f"{len(trashed)}-{Path(path).name}"))

    monkeypatch.setattr(actions_mod, "send2trash", fake_trash)
    db = Database(tmp_dirs[0] / "test.db")
    with TestClient(create_app(db)) as client:
        r = client.post("/api/scans", json={"root": str(fixtures_dir),
                                            "kinds": ["exact", "similar"]})
        scan_id = r.json()["scan_id"]
        deadline = time.monotonic() + 60
        while client.get(f"/api/scans/{scan_id}").json()["status"] != "done":
            assert time.monotonic() < deadline
            time.sleep(0.2)
        yield client, db, scan_id, trashed


def _group(client, scan_id, index=0):
    gid = client.get(f"/api/scans/{scan_id}/groups",
                     params={"sort": "count"}).json()["groups"][index]["id"]
    return client.get(f"/api/groups/{gid}").json()


def _decide(client, group, keep=(), discard=()):
    decisions = {str(i): "keep" for i in keep}
    decisions.update({str(i): "discard" for i in discard})
    assert client.post(f"/api/groups/{group['id']}/decisions",
                       json={"decisions": decisions}).status_code == 200


def test_dry_run_changes_nothing(env):
    client, db, scan_id, trashed = env
    g = _group(client, scan_id)
    ids = [m["image_id"] for m in g["members"]]
    _decide(client, g, keep=ids[:1], discard=ids[1:])
    plan = client.post(f"/api/scans/{scan_id}/apply", params={"dry_run": "true"}).json()
    assert plan["dry_run"] is True and plan["moved"] == len(ids) - 1
    assert trashed == []
    assert all(Path(m["path"]).exists() for m in g["members"])
    assert client.get(f"/api/groups/{g['id']}").status_code == 200


def test_apply_updates_database_and_is_idempotent(env):
    client, db, scan_id, trashed = env
    before = client.get(f"/api/scans/{scan_id}").json()
    g = _group(client, scan_id)
    ids = [m["image_id"] for m in g["members"]]
    _decide(client, g, keep=ids[:1], discard=ids[1:])

    result = client.post(f"/api/scans/{scan_id}/apply").json()
    assert result["moved"] == len(ids) - 1 and result["errors"] == []

    after = client.get(f"/api/scans/{scan_id}").json()
    assert after["images_found"] == before["images_found"] - result["moved"]
    assert after["exact_groups"] + after["similar_groups"] < (
        before["exact_groups"] + before["similar_groups"])
    for gr in client.get(f"/api/scans/{scan_id}/groups").json()["groups"]:
        # every listed group is still browsable with a live representative
        assert client.get(gr["thumb_url"]).status_code == 200
        assert client.get(f"/api/groups/{gr['id']}").json()["members"]

    # nothing marked any more: a second apply does nothing and reports no errors
    again = client.post(f"/api/scans/{scan_id}/apply").json()
    assert again["moved"] == 0 and again["errors"] == [] and again["skipped"] == []
    assert len(trashed) == result["moved"]


def test_never_trashes_every_copy_of_a_group(env):
    client, db, scan_id, trashed = env
    g = _group(client, scan_id)
    _decide(client, g, discard=[m["image_id"] for m in g["members"]])
    result = client.post(f"/api/scans/{scan_id}/apply").json()
    assert result["moved"] == 0 and trashed == []
    assert g["id"] in result["blocked_groups"]
    assert all(Path(m["path"]).exists() for m in g["members"])
    assert {s["reason"] for s in result["skipped"]} == {
        "would leave its group with no copy"}


def test_missing_survivors_do_not_count(env):
    """If the only kept copy is gone (unmounted drive, deleted), trash nothing."""
    client, db, scan_id, trashed = env
    g = _group(client, scan_id)
    ids = [m["image_id"] for m in g["members"]]
    _decide(client, g, keep=ids[:1], discard=ids[1:])
    os.remove(g["members"][0]["path"])
    result = client.post(f"/api/scans/{scan_id}/apply").json()
    assert result["moved"] == 0 and trashed == []
    assert all(Path(m["path"]).exists() for m in g["members"][1:])


def test_missing_discard_target_is_reported_not_forgotten(env):
    client, db, scan_id, trashed = env
    g = _group(client, scan_id)
    ids = [m["image_id"] for m in g["members"]]
    _decide(client, g, keep=ids[:1], discard=ids[1:])
    os.remove(g["members"][1]["path"])
    result = client.post(f"/api/scans/{scan_id}/apply").json()
    assert result["moved"] == len(ids) - 2
    assert any(s["path"] == g["members"][1]["path"] and s["reason"] == "file is missing"
               for s in result["skipped"])
    # its record is kept so a remounted drive still works
    assert db.get_image(g["members"][1]["image_id"]) is not None


def test_changed_file_is_not_trashed(env):
    client, db, scan_id, trashed = env
    g = _group(client, scan_id)
    ids = [m["image_id"] for m in g["members"]]
    _decide(client, g, keep=ids[:1], discard=ids[1:])
    target = Path(g["members"][1]["path"])
    target.write_bytes(target.read_bytes() + b"edited after review")
    result = client.post(f"/api/scans/{scan_id}/apply").json()
    assert str(target) not in trashed and target.exists()
    assert any("changed since the scan" in s["reason"] for s in result["skipped"])


def test_symlink_and_outside_root_are_refused(env, tmp_path):
    client, db, scan_id, trashed = env
    g = _group(client, scan_id)
    ids = [m["image_id"] for m in g["members"]]
    _decide(client, g, keep=ids[:1], discard=ids[1:])
    # swap a reviewed file for a symlink to somewhere else
    victim = tmp_path / "precious.jpg"
    victim.write_bytes(b"do not delete")
    target = Path(g["members"][1]["path"])
    target.unlink()
    target.symlink_to(victim)
    result = client.post(f"/api/scans/{scan_id}/apply").json()
    assert victim.exists() and str(target) not in trashed
    assert any(s["reason"] == "is a symbolic link" for s in result["skipped"])


def test_keep_in_another_group_protects_image(env):
    client, db, scan_id, trashed = env
    g = _group(client, scan_id)
    ids = [m["image_id"] for m in g["members"]]
    _decide(client, g, keep=ids[:1], discard=ids[1:])
    # same image also sits in a second group where it is kept
    other = db._execute("INSERT INTO groups (scan_id, kind, rep_image_id) VALUES (?, 'similar', ?)",
                        (scan_id, ids[1])).lastrowid
    db._executemany("INSERT INTO group_members (group_id, image_id, decision) VALUES (?, ?, ?)",
                    [(other, ids[1], "keep"), (other, ids[0], "undecided")])
    result = client.post(f"/api/scans/{scan_id}/apply").json()
    assert result["moved"] == len(ids) - 2
    assert Path(g["members"][1]["path"]).exists()
    assert any(s["reason"] == "kept or undecided in another group" for s in result["skipped"])


def test_apply_refused_while_scan_running_or_unfinished(env):
    client, db, scan_id, trashed = env
    db.update_scan(scan_id, status="error")
    assert client.post(f"/api/scans/{scan_id}/apply").status_code == 409
    assert client.post("/api/scans/999999/apply").status_code == 404


def test_concurrent_apply_is_rejected(env):
    client, db, scan_id, trashed = env
    assert app_mod._APPLY_LOCK.acquire(blocking=False)
    try:
        assert client.post(f"/api/scans/{scan_id}/apply").status_code == 409
    finally:
        app_mod._APPLY_LOCK.release()


def test_delete_scan_blocked_only_by_live_thread(env):
    client, db, scan_id, trashed = env
    app_mod._APPLYING["scan_id"] = scan_id
    try:
        assert client.delete(f"/api/scans/{scan_id}").status_code == 409
    finally:
        app_mod._APPLYING["scan_id"] = None
    app_mod._RUNNING[scan_id] = object()  # type: ignore[assignment]
    try:
        assert client.delete(f"/api/scans/{scan_id}").status_code == 409
    finally:
        app_mod._RUNNING.pop(scan_id, None)
    # a stale 'running' row (crashed process) can be deleted
    db.update_scan(scan_id, status="running")
    assert client.delete(f"/api/scans/{scan_id}").status_code == 200


def test_delete_scan_removes_everything_and_leaves_files(env, fixtures_dir):
    client, db, scan_id, trashed = env
    files_before = sorted(os.listdir(fixtures_dir))
    assert client.delete(f"/api/scans/{scan_id}").status_code == 200
    assert sorted(os.listdir(fixtures_dir)) == files_before
    for table in ("images", "groups"):
        assert db._query(f"SELECT COUNT(*) FROM {table}")[0][0] == 0
    assert db._query("SELECT COUNT(*) FROM group_members")[0][0] == 0
    assert db.delete_scan(scan_id) is False
