"""API tests for the review web app (httpx TestClient)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cosuil.server.app import create_app

MOCK_TRASH: list[str] = []


@pytest.fixture()
def client(tmp_dirs, fixtures_dir, monkeypatch):
    MOCK_TRASH.clear()
    import cosuil.actions as actions_mod

    def fake_trash(path: str):
        MOCK_TRASH.append(path)

    monkeypatch.setattr(actions_mod, "send2trash", fake_trash)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _wait_scan(client, scan_id: int, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scan = client.get(f"/api/scans/{scan_id}").json()
        if scan["status"] in ("done", "error"):
            return scan
        time.sleep(0.2)
    raise TimeoutError("scan did not finish")


def _first_group(client, scan_id: int) -> dict:
    listing = client.get(f"/api/scans/{scan_id}/groups", params={"sort": "count"}).json()
    assert listing["total"] >= 1
    gid = listing["groups"][0]["id"]
    return client.get(f"/api/groups/{gid}").json()


def test_browse_endpoint(client):
    data = client.get("/api/browse", params={"path": "/"}).json()
    assert "dirs" in data and data["path"] == "/"


def test_full_review_flow(client, fixtures_dir):
    # start a scan
    r = client.post(
        "/api/scans",
        json={"root": str(fixtures_dir), "kinds": ["exact", "similar"]},
    )
    assert r.status_code == 200
    scan_id = r.json()["scan_id"]

    scan = _wait_scan(client, scan_id)
    assert scan["status"] == "done"
    assert scan["images_found"] >= 10
    # scan settings are exposed for display
    assert scan["config"]["phash_threshold"] == 6
    assert set(scan["config"]["kinds"]) == {"exact", "similar"}
    assert scan["config"]["skip_libraries"] is True

    # group listing + detail
    group = _first_group(client, scan_id)
    members = group["members"]
    assert len(members) >= 2
    assert all("thumb_url" in m and "file_url" in m for m in members)
    # location info for the compare view
    assert all("dirname" in m and "rel_dir" in m for m in members)
    assert group["root"] == str(fixtures_dir)
    first_image = members[0]["image_id"]

    # thumbnail endpoint returns a JPEG
    t = client.get(f"/api/images/{first_image}/thumb")
    assert t.status_code == 200
    assert t.headers["content-type"] == "image/jpeg"

    # file endpoint
    f = client.get(f"/api/images/{first_image}/file")
    assert f.status_code == 200

    # decisions: keep the first member, discard the rest
    decisions = {str(m["image_id"]): "discard" for m in members}
    decisions[str(first_image)] = "keep"
    r = client.post(
        f"/api/groups/{group['id']}/decisions", json={"decisions": decisions}
    )
    assert r.status_code == 200

    # apply moves the discards to (mock) trash
    r = client.post(f"/api/scans/{scan_id}/apply")
    assert r.status_code == 200
    result = r.json()
    assert result["moved"] == len(members) - 1
    assert len(MOCK_TRASH) == len(members) - 1
    assert Path(result["report_file"]).exists()

    # group status is now reviewed
    detail = client.get(f"/api/groups/{group['id']}").json()
    assert detail["status"] == "reviewed"


def test_scan_of_missing_dir_rejected(client):
    r = client.post("/api/scans", json={"root": "/definitely/not/here"})
    assert r.status_code == 400


def test_scan_history_and_rescan(client, fixtures_dir):
    r = client.post(
        "/api/scans",
        json={"root": str(fixtures_dir), "kinds": ["exact", "similar"]},
    )
    assert r.status_code == 200
    scan_id = r.json()["scan_id"]
    _wait_scan(client, scan_id)

    # history endpoint lists the scan
    history = client.get("/api/scans").json()["scans"]
    assert any(s["id"] == scan_id for s in history)

    # rescan is incremental: only the unreadable file gets re-hashed
    r = client.post(f"/api/scans/{scan_id}/rescan")
    assert r.status_code == 200
    new_id = r.json()["scan_id"]
    assert new_id != scan_id
    scan2 = _wait_scan(client, new_id)
    assert scan2["status"] == "done"
    assert scan2["images_found"] >= 10
    assert scan2["images_hashed"] <= 1

    # rescan of an unknown scan 404s
    assert client.post("/api/scans/999999/rescan").status_code == 404


def test_delete_scan(client, fixtures_dir):
    r = client.post(
        "/api/scans",
        json={"root": str(fixtures_dir), "kinds": ["exact", "similar"]},
    )
    scan_id = r.json()["scan_id"]
    _wait_scan(client, scan_id)
    assert client.get(f"/api/scans/{scan_id}").status_code == 200

    r = client.delete(f"/api/scans/{scan_id}")
    assert r.status_code == 200
    assert client.get(f"/api/scans/{scan_id}").status_code == 404
    assert client.get(f"/api/scans/{scan_id}/groups").status_code == 404
    history = client.get("/api/scans").json()["scans"]
    assert all(s["id"] != scan_id for s in history)
    # deleting an unknown scan 404s
    assert client.delete("/api/scans/999999").status_code == 404


def test_unknown_scan_404(client):
    assert client.get("/api/scans/999999").status_code == 404
