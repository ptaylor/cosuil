"""Thumbnail generation with an on-disk cache (atomic writes)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PIL import Image, ImageOps

from .config import thumbs_dir
from .hashing import open_loaded, register_extra_openers

register_extra_openers()


def _key(path: str, mtime_ns: int, size: int) -> str:
    raw = f"{path}|{mtime_ns}|{size}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()


def thumb_path(path: str, mtime_ns: int, size: int) -> Path:
    return thumbs_dir() / f"{_key(path, mtime_ns, size)}.jpg"


def _generate(source: str, mtime_ns: int, size: int, thumb_size: int, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.jpg")
    with open_loaded(source) as im:
        try:
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((thumb_size, thumb_size))
        im.save(tmp, "JPEG", quality=85)
    os.replace(tmp, dest)  # atomic: safe against concurrent generation


def get_thumbnail(
    path: str, mtime_ns: int, size: int, thumb_size: int = 256
) -> Path | None:
    """Return the cached thumbnail path, generating it on first request."""
    dest = thumb_path(path, mtime_ns, size)
    if dest.exists():
        return dest
    try:
        _generate(path, mtime_ns, size, thumb_size, dest)
    except Exception:
        return None
    return dest if dest.exists() else None


# Formats browsers can display natively; everything else is transcoded.
BROWSER_FORMATS = frozenset({"JPEG", "JPG", "PNG", "GIF", "WEBP", "BMP"})
_PREVIEW_MAX_DIM = 2560


def _preview_key(path: str, mtime_ns: int, size: int) -> str:
    raw = f"full|{path}|{mtime_ns}|{size}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()


def get_preview(path: str, mtime_ns: int, size: int, fmt: str | None) -> Path | None:
    """Path to a browser-displayable version of *path*.

    Native web formats are served as-is; others (Netpbm family, HEIC, TIFF…)
    are transcoded to PNG on first request and cached.
    """
    original = Path(path)
    if (fmt or "").upper() in BROWSER_FORMATS:
        return original if original.exists() else None
    dest = thumbs_dir() / f"{_preview_key(path, mtime_ns, size)}.png"
    if dest.exists():
        return dest
    tmp = dest.with_suffix(".tmp.png")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open_loaded(original) as im:
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass
            im.thumbnail((_PREVIEW_MAX_DIM, _PREVIEW_MAX_DIM))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(tmp, "PNG")
        os.replace(tmp, dest)
    except Exception:
        return None
    return dest if dest.exists() else None
