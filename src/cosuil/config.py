"""Configuration for cosuil."""

from __future__ import annotations

import os
import tomllib
import warnings
from dataclasses import dataclass, field, fields
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir

APP_NAME = "cosuil"

# fmt: off
# Core formats named in the requirements, plus the Netpbm family and similar.
DEFAULT_EXTENSIONS: tuple[str, ...] = (
    # GIF, JPEG (all variants), PNG, Google's WebP
    ".gif", ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp",
    # Apple HEIC / HEIF container (+ variant spellings)
    ".heic", ".heif", ".hif",
    # Netpbm family: PBM / PGM / PPM / PNM / PAM
    ".pbm", ".pgm", ".ppm", ".pnm", ".pam",
    # Similar simple bitmap formats
    ".xbm", ".xpm",
    # Best-effort extras
    ".avif", ".bmp", ".tif", ".tiff",
)
# fmt: on

SCAN_KINDS: tuple[str, ...] = ("exact", "similar", "deep")

DEFAULT_PHASH_THRESHOLD = 6
DEFAULT_CNN_THRESHOLD = 0.85
DEFAULT_THUMB_SIZE = 256


def config_dir() -> Path:
    override = os.environ.get("COSUIL_CONFIG_DIR")
    if override:
        return Path(override)
    xdg = Path.home() / ".config" / APP_NAME
    platform = Path(user_config_dir(APP_NAME))
    # Prefer the XDG-style location (matches the README); fall back to the
    # platform-native directory when a config already exists there.
    if xdg.exists() or not platform.exists():
        return xdg
    return platform


def data_dir() -> Path:
    return Path(os.environ.get("COSUIL_DATA_DIR", user_data_dir(APP_NAME)))


def cache_dir() -> Path:
    return Path(os.environ.get("COSUIL_CACHE_DIR", user_cache_dir(APP_NAME)))


def db_path() -> Path:
    return data_dir() / "cosuil.db"


def thumbs_dir() -> Path:
    return cache_dir() / "thumbnails"


def reports_dir() -> Path:
    return data_dir() / "reports"


def _load_user_toml() -> dict:
    path = config_dir() / "config.toml"
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:  # pragma: no cover - bad user config
        warnings.warn(f"could not read {path}: {exc}", stacklevel=2)
        return {}


def _coerce_tuple(value) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return tuple(str(v).lower() for v in value)
    return None


@dataclass
class QualityWeights:
    """Weights for the composite per-image quality score (all 0..1)."""

    resolution: float = 0.30
    sharpness: float = 0.25
    size: float = 0.15
    format: float = 0.10
    exif: float = 0.20

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class ScanConfig:
    root: Path = Path(".")
    kinds: tuple[str, ...] = ("exact", "similar")
    phash_threshold: int = DEFAULT_PHASH_THRESHOLD
    cnn_threshold: float = DEFAULT_CNN_THRESHOLD
    include_hidden: bool = False
    skip_libraries: bool = True  # skip macOS Photos Library bundles
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    workers: int = 0  # 0 = auto
    thumb_size: int = DEFAULT_THUMB_SIZE
    fresh: bool = False  # True = do not reuse hashes from previous scans
    quality_weights: QualityWeights = field(default_factory=QualityWeights)

    @classmethod
    def from_toml(cls, root: Path, kinds: tuple[str, ...], phash_threshold: int,
                  cnn_threshold: float, include_hidden: bool, extensions: tuple[str, ...],
                  workers: int, thumb_size: int, fresh: bool) -> "ScanConfig":
        cfg = cls(
            root=root,
            kinds=kinds,
            phash_threshold=phash_threshold,
            cnn_threshold=cnn_threshold,
            include_hidden=include_hidden,
            extensions=extensions,
            workers=workers,
            thumb_size=thumb_size,
            fresh=fresh,
        )
        user = _load_user_toml()
        scan_section = user.get("scan", {})
        if cfg.phash_threshold == DEFAULT_PHASH_THRESHOLD:
            cfg.phash_threshold = int(scan_section.get("phash_threshold", cfg.phash_threshold))
        if cfg.cnn_threshold == DEFAULT_CNN_THRESHOLD:
            cfg.cnn_threshold = float(scan_section.get("cnn_threshold", cfg.cnn_threshold))
        if cfg.thumb_size == DEFAULT_THUMB_SIZE:
            cfg.thumb_size = int(scan_section.get("thumb_size", cfg.thumb_size))
        if "include_hidden" in scan_section:
            cfg.include_hidden = bool(scan_section["include_hidden"])
        if "skip_libraries" in scan_section:
            cfg.skip_libraries = bool(scan_section["skip_libraries"])
        ext = _coerce_tuple(scan_section.get("extensions"))
        if ext is not None:
            cfg.extensions = ext

        weights = user.get("quality", {})
        qw = cfg.quality_weights
        for name in fields(qw):
            if name.name in weights:
                setattr(qw, name.name, float(weights[name.name]))
        cfg.quality_weights = qw
        return cfg

    def to_json(self) -> dict:
        data = {
            "root": str(self.root),
            "kinds": list(self.kinds),
            "phash_threshold": self.phash_threshold,
            "cnn_threshold": self.cnn_threshold,
            "include_hidden": self.include_hidden,
            "skip_libraries": self.skip_libraries,
            "extensions": list(self.extensions),
            "workers": self.workers,
            "thumb_size": self.thumb_size,
            "quality_weights": self.quality_weights.as_dict(),
        }
        return data
