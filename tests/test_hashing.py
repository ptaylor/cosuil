"""Tests for hashing and large-image warning attribution."""

from __future__ import annotations

import shutil

from PIL import Image as PILImage

from cosuil.hashing import phash_image


def test_large_image_warning_recorded_with_dimensions(tmp_path, monkeypatch):
    img = PILImage.new("RGB", (64, 64), (200, 30, 30))
    p = tmp_path / "big.jpg"
    img.save(p, "JPEG")
    # 64x64 = 4096 px > 3000 limit -> warning, < 6000 -> no error
    monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 3000)
    info = phash_image(str(p))
    assert info.phash is not None
    assert info.warning is not None
    assert "64×64" in info.warning


def test_normal_image_has_no_warning(tmp_path):
    img = PILImage.new("RGB", (32, 32), (10, 200, 10))
    p = tmp_path / "small.jpg"
    img.save(p, "JPEG")
    info = phash_image(str(p))
    assert info.warning is None
    assert info.error is None
    assert info.phash is not None


def test_truncated_jpeg_hashed_with_warning(tmp_path):
    img = PILImage.new("RGB", (64, 64), (20, 20, 20))
    p = tmp_path / "trunc.jpg"
    img.save(p, "JPEG", quality=90)
    data = p.read_bytes()
    p.write_bytes(data[:-6])  # chop the tail off the JPEG stream
    info = phash_image(str(p))
    assert info.error is None
    assert info.phash is not None
    assert "truncated" in (info.warning or "")


def test_truncated_jpeg_still_produces_thumbnail(tmp_path, tmp_dirs):
    from cosuil.thumbs import get_thumbnail

    img = PILImage.new("RGB", (64, 64), (90, 90, 90))
    p = tmp_path / "t.jpg"
    img.save(p, "JPEG", quality=90)
    data = p.read_bytes()
    p.write_bytes(data[:-6])
    thumb = get_thumbnail(str(p), p.stat().st_mtime_ns, p.stat().st_size)
    assert thumb is not None and thumb.exists()


def test_truncated_jpeg_phash_matches_intact_copy(tmp_path):
    # the padded decode should land close enough to the intact original's phash
    import numpy as np

    img = PILImage.fromarray((np.random.rand(256, 256, 3) * 255).astype("uint8"))
    intact = tmp_path / "intact.jpg"
    img.save(intact, "JPEG", quality=90)
    trunc = tmp_path / "trunc.jpg"
    shutil.copyfile(intact, trunc)
    data = trunc.read_bytes()
    trunc.write_bytes(data[:-6])
    a = phash_image(str(intact))
    b = phash_image(str(trunc))
    assert a.phash and b.phash
    assert bin(int(a.phash, 16) ^ int(b.phash, 16)).count("1") <= 8
