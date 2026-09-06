"""Shared test fixtures: isolated data dirs and a generated image set."""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_dirs(tmp_path, monkeypatch):
    """Redirect cosuil data/cache dirs to a per-test temp location."""
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    monkeypatch.setenv("COSUIL_DATA_DIR", str(data))
    monkeypatch.setenv("COSUIL_CACHE_DIR", str(cache))
    return data, cache


def _make_image(seed: int, size: tuple[int, int] = (512, 512)):
    from PIL import Image, ImageDraw

    rng = random.Random(seed)
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    top = (rng.randint(40, 120), rng.randint(40, 120), rng.randint(40, 120))
    bottom = (rng.randint(130, 220), rng.randint(130, 220), rng.randint(130, 220))
    for y in range(size[1]):
        t = y / size[1]
        draw.line(
            [(0, y), (size[0], y)],
            fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)),
        )
    for _ in range(rng.randint(6, 12)):
        x0, y0 = rng.randint(0, 480), rng.randint(0, 480)
        draw.ellipse(
            [x0, y0, x0 + rng.randint(20, 90), y0 + rng.randint(20, 90)],
            fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)),
        )
    for _ in range(rng.randint(3, 6)):
        x0, x1 = sorted([rng.randint(0, 440), rng.randint(0, 440)])
        y0, y1 = sorted([rng.randint(0, 440), rng.randint(0, 440)])
        draw.rectangle([x0, y0, x1, y1], outline=(255, 255, 255), width=4)
    return img


def _watermark(img, text: str = "SAMPLE"):
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    draw.text((24, 24), text, fill=(255, 255, 0))
    return img


def build_fixture_set(dest: Path) -> Path:
    """Create a deterministic set of duplicate/similar/distinct images.

    Returns the fixture directory.
    """
    dest.mkdir(parents=True, exist_ok=True)
    base = _make_image(seed=7)

    base.save(dest / "base.jpg", "JPEG", quality=92)
    # byte-identical copy with a different extension variant
    shutil.copyfile(dest / "base.jpg", dest / "base-copy.jpeg")
    # near duplicates
    base.save(dest / "base-recompressed.jpg", "JPEG", quality=55)
    resized = base.resize((384, 384))
    resized.save(dest / "base-resized.jpg", "JPEG", quality=92)
    _watermark(base.copy()).save(dest / "base-watermarked.jpg", "JPEG", quality=92)
    # same content, other formats
    base.save(dest / "base.png", "PNG")
    base.save(dest / "base.webp", "WEBP", quality=90)
    base.save(dest / "base.ppm", "PPM")
    # uppercase extension variant
    base.convert("L").save(dest / "base-grey.PGM", "PPM")
    # a completely different image
    _make_image(seed=999).save(dest / "other.jpg", "JPEG", quality=92)
    # heic, if the writer is available
    try:
        base.save(dest / "base.heic", "HEIF", quality=90)
    except Exception:
        pass
    # an unreadable file with an image extension
    (dest / "broken.jpg").write_bytes(b"not a real jpeg at all")
    return dest


@pytest.fixture()
def fixtures_dir(tmp_dirs) -> Path:
    return build_fixture_set(tmp_dirs[0] / "pictures")
