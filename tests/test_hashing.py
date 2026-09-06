"""Tests for hashing and large-image warning attribution."""

from __future__ import annotations

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
