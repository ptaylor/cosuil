"""Unit tests for file discovery and extension variant handling."""

from __future__ import annotations

from cosuil.discovery import iter_image_files


def _touch(base, name, content=b"x"):
    p = base / name
    p.write_bytes(content)
    return p


def test_extension_variants_case_insensitive(tmp_path):
    files = [
        _touch(tmp_path, "a.JPG"),
        _touch(tmp_path, "b.jpeg"),
        _touch(tmp_path, "c.jfif"),
        _touch(tmp_path, "d.GIF"),
        _touch(tmp_path, "e.webp"),
        _touch(tmp_path, "f.HEIC"),
        _touch(tmp_path, "g.pnm"),
        _touch(tmp_path, "h.txt"),
        _touch(tmp_path, "i.jpg.bak"),
    ]
    found = {f.path.split("/")[-1] for f in iter_image_files(tmp_path, [".jpg", ".jpeg", ".jfif", ".gif", ".webp", ".heic", ".pnm"])}
    assert found == {"a.JPG", "b.jpeg", "c.jfif", "d.GIF", "e.webp", "f.HEIC", "g.pnm"}


def test_hidden_skipped_by_default(tmp_path):
    _touch(tmp_path, "visible.jpg")
    _touch(tmp_path, ".hidden.jpg")
    (tmp_path / ".hiddendir").mkdir()
    _touch(tmp_path / ".hiddendir", "inside.jpg")
    found = [f.path for f in iter_image_files(tmp_path, [".jpg"])]
    assert len(found) == 1 and found[0].endswith("visible.jpg")


def test_hidden_included_when_requested(tmp_path):
    _touch(tmp_path, ".hidden.jpg")
    found = [f.path for f in iter_image_files(tmp_path, [".jpg"], include_hidden=True)]
    assert len(found) == 1


def test_progress_callback(tmp_path):
    for i in range(5):
        _touch(tmp_path, f"img{i}.jpg")
    _touch(tmp_path, "not-image.txt")
    walked, matched = [], []

    def on_progress(w, m):
        walked.append(w)
        matched.append(m)

    list(iter_image_files(tmp_path, [".jpg"], on_progress=on_progress))
    assert walked[-1] == 6
    assert matched[-1] == 5
