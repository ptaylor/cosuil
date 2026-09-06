"""Hashing: exact (BLAKE3) and perceptual (phash) plus per-image quality capture.

Every image is decoded exactly once during the perceptual pass and yields:
the perceptual hash, dimensions, format, sharpness, EXIF bits and an error
message when decoding fails. The BLAKE3 pass only touches the file bytes.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field

import blake3
import imagehash
from PIL import Image, ImageFilter, ImageOps, ImageStat
from PIL.Image import DecompressionBombWarning

from .quality import composite_score

_OPENERS_REGISTERED = False


def register_extra_openers() -> None:
    """Register pillow-heif (HEIC) and pillow-avif (AVIF) plugins, when installed."""
    global _OPENERS_REGISTERED
    if _OPENERS_REGISTERED:
        return
    _OPENERS_REGISTERED = True
    for name in ("pillow_heif", "pillow_avif"):
        try:
            mod = __import__(name)
        except ImportError:
            continue
        register = getattr(mod, "register_heif_opener", None) or getattr(
            mod, "register_avif_opener", None
        )
        if register is not None:
            try:
                register()
            except Exception:  # pragma: no cover - plugin registration is best-effort
                pass


def blake3_file(path: str) -> str:
    """BLAKE3 hex digest of file contents (streamed)."""
    hasher = blake3.blake3()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_exif(im: Image.Image) -> dict:
    exif: dict = {}
    try:
        ex = im.getexif()
        camera = ex.get(272)  # Model
        if camera:
            value = str(camera).strip("\x00 ").strip()
            if value:
                exif["camera"] = value
        taken = None
        try:
            taken = ex.get_ifd(0x8769).get(36867)  # DateTimeOriginal
        except Exception:
            pass
        if not taken:
            taken = ex.get(306)  # DateTime
        if taken:
            value = str(taken).strip("\x00 ").strip()
            if value:
                exif["taken"] = value
    except Exception:
        pass
    return exif


@dataclass
class ImageInfo:
    path: str
    phash: str | None = None
    width: int | None = None
    height: int | None = None
    format: str | None = None
    sharpness: float | None = None
    exif: dict = field(default_factory=dict)
    quality_score: float | None = None
    quality_factors: dict = field(default_factory=dict)
    warning: str | None = None
    error: str | None = None


def phash_image(path: str, hash_size: int = 16, thumb_probe: bool = False) -> ImageInfo:
    """Decode *path* once and compute phash + quality metadata. Never raises."""
    info = ImageInfo(path=path)
    im: Image.Image | None = None
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DecompressionBombWarning)
            im = Image.open(path)
        for w in caught:
            if issubclass(w.category, DecompressionBombWarning) and im is not None:
                w_px, h_px = im.size
                info.warning = f"very large image ({w_px}×{h_px} px)"
        info.width, info.height = im.size
        info.format = (im.format or os.path.splitext(path)[1].lstrip(".")).upper()
        info.exif = _read_exif(im)

        try:
            im.draft("L", (512, 512))  # fast path for JPEG; no-op elsewhere
        except Exception:
            pass

        try:
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass

        gray = im.convert("L")
        try:
            ph = imagehash.phash(gray, hash_size=hash_size)
            # `.hash` is a numpy array in imagehash >= 4.2; str(ph) is the hex form
            info.phash = f"{int(str(ph), 16):016x}"
        except Exception as exc:
            info.error = f"phash: {exc}"
            return info

        # Sharpness: variance of a 3x3 Laplacian over a downscaled copy.
        try:
            small = gray.copy()
            small.thumbnail((512, 512))
            lap = small.filter(
                ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=0)
            )
            info.sharpness = ImageStat.Stat(lap).stddev ** 2
        except Exception:
            info.sharpness = None

        info.quality_score, info.quality_factors = composite_score(
            width=info.width or 0,
            height=info.height or 0,
            size=os.path.getsize(path) if os.path.exists(path) else 0,
            fmt=info.format or "",
            sharpness=info.sharpness,
            exif=info.exif,
        )
    except Exception as exc:
        info.error = f"{type(exc).__name__}: {exc}"
    finally:
        if im is not None:
            try:
                im.close()
            except Exception:
                pass
    return info


def phash_int(info: ImageInfo) -> int | None:
    if info.phash is None:
        return None
    return int(info.phash, 16)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


__all__ = [
    "ImageInfo",
    "blake3_file",
    "hamming",
    "phash_image",
    "phash_int",
    "register_extra_openers",
]
