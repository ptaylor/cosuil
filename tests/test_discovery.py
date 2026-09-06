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


def test_photos_library_skipped_by_default(tmp_path):
    lib = tmp_path / "Photos Library.photoslibrary"
    (lib / "resources").mkdir(parents=True)
    _touch(lib / "resources", "derivative.jpg")
    _touch(tmp_path, "normal.jpg")
    found = [f.path for f in iter_image_files(tmp_path, [".jpg"])]
    assert len(found) == 1 and found[0].endswith("normal.jpg")


def test_photos_library_included_when_requested(tmp_path):
    lib = tmp_path / "Photos Library.photoslibrary"
    (lib / "resources").mkdir(parents=True)
    _touch(lib / "resources", "derivative.jpg")
    found = [f.path for f in iter_image_files(tmp_path, [".jpg"], skip_libraries=False)]
    assert len(found) == 1 and "photoslibrary" in found[0]


def test_exclude_dir_by_name(tmp_path):
    (tmp_path / "Takeout").mkdir()
    _touch(tmp_path / "Takeout", "a.jpg")
    _touch(tmp_path, "keep.jpg")
    found = [f.path for f in iter_image_files(tmp_path, [".jpg"], exclude=("Takeout",))]
    assert len(found) == 1 and found[0].endswith("keep.jpg")


def test_exclude_dir_by_absolute_path(tmp_path):
    (tmp_path / "Takeout").mkdir()
    _touch(tmp_path / "Takeout", "a.jpg")
    _touch(tmp_path, "keep.jpg")
    found = list(iter_image_files(tmp_path, [".jpg"], exclude=(str(tmp_path / "Takeout"),)))
    assert len(found) == 1 and found[0].path.endswith("keep.jpg")


def test_exclude_dir_absolute_path_case_insensitive(tmp_path):
    # a lowercase rule must still match an on-disk directory named 'Takeout'
    (tmp_path / "Takeout").mkdir()
    _touch(tmp_path / "Takeout", "a.jpg")
    _touch(tmp_path, "keep.jpg")
    lower = str(tmp_path / "takeout")
    found = list(iter_image_files(tmp_path, [".jpg"], exclude=(lower,)))
    assert len(found) == 1 and found[0].path.endswith("keep.jpg")
