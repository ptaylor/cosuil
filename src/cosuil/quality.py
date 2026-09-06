"""Per-image quality scoring used to help humans pick keepers.

Factors are normalized to 0..1 and combined with configurable weights:
  - resolution  (megapixels)
  - sharpness   (variance of a Laplacian filter)
  - size        (file bytes, log-scaled)
  - format      (lossless > lossy)
  - exif        (has camera / capture date)
"""

from __future__ import annotations

import math

# Preference order for formats (lossless originals first).
FORMAT_SCORES: dict[str, float] = {
    "PNG": 1.0,
    "PPM": 1.0,
    "PGM": 1.0,
    "PBM": 1.0,
    "PNM": 1.0,
    "PAM": 1.0,
    "TIFF": 1.0,
    "BMP": 0.95,
    "XBM": 0.8,
    "XPM": 0.8,
    "WEBP": 0.9,
    "AVIF": 0.9,
    "HEIC": 0.85,
    "HEIF": 0.85,
    "JPEG": 0.75,
    "JPG": 0.75,
    "GIF": 0.7,
}


def format_score(fmt: str | None) -> float:
    return FORMAT_SCORES.get((fmt or "").upper(), 0.8)


def resolution_score(width: int, height: int) -> float:
    if not width or not height:
        return 0.0
    mp = (width * height) / 1_000_000.0
    return min(1.0, mp / 12.0)  # 12 MP = full marks


def sharpness_score(variance: float | None) -> float:
    if variance is None:
        return 0.5
    return 1.0 - math.exp(-variance / 400.0)


def size_score(size_bytes: int) -> float:
    if size_bytes <= 0:
        return 0.0
    return min(1.0, math.log1p(size_bytes) / math.log1p(15_000_000))


def exif_score(exif: dict | None) -> float:
    exif = exif or {}
    has_camera = bool(exif.get("camera"))
    has_taken = bool(exif.get("taken"))
    if has_camera and has_taken:
        return 1.0
    if has_camera or has_taken:
        return 0.65
    return 0.3


def composite_score(
    width: int,
    height: int,
    size: int,
    fmt: str,
    sharpness: float | None,
    exif: dict | None,
    weights: dict | None = None,
) -> tuple[float, dict]:
    """Return (total 0..1, factors dict of each normalized component)."""
    w = weights or {
        "resolution": 0.30,
        "sharpness": 0.25,
        "size": 0.15,
        "format": 0.10,
        "exif": 0.20,
    }
    factors = {
        "resolution": resolution_score(width, height),
        "sharpness": sharpness_score(sharpness),
        "size": size_score(size),
        "format": format_score(fmt),
        "exif": exif_score(exif),
    }
    total = sum(w.get(k, 0.0) * v for k, v in factors.items())
    return round(min(1.0, max(0.0, total)), 4), {
        k: round(v, 4) for k, v in factors.items()
    }
