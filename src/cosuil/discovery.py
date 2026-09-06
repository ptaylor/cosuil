"""File discovery: walk a directory tree and match image extensions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Collection, Iterator

# Callback signature: (files_walked, images_matched)
ProgressFn = Callable[[int, int], None]


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    mtime_ns: int


def _excluded(dirpath: str, rules: Collection[str]) -> bool:
    """Match an exclusion rule: absolute/tilde paths match exactly; bare
    names match any directory with that name."""
    for rule in rules:
        rule = rule.strip()
        if not rule:
            continue
        if rule.startswith(("/", "~")):
            if os.path.abspath(os.path.expanduser(rule)) == dirpath:
                return True
        elif os.path.basename(dirpath) == rule:
            return True
    return False


def iter_image_files(
    root: str | os.PathLike,
    extensions: Collection[str],
    include_hidden: bool = False,
    skip_libraries: bool = True,
    exclude: Collection[str] = (),
    on_progress: ProgressFn | None = None,
) -> Iterator[FileInfo]:
    """Yield image files under *root*, matching extensions case-insensitively.

    Hidden files/directories are skipped unless *include_hidden* is set.
    macOS Photos Library bundles (`.photoslibrary`) are skipped unless
    *skip_libraries* is False — their derivatives folders are cache files,
    not user-managed duplicates. Directories matching *exclude* (absolute
    paths or bare directory names) are skipped. Symlinked directories are
    not followed.
    """
    ext_set = {str(e).lower() for e in extensions}
    root = os.path.abspath(os.fspath(root))
    walked = 0
    matched = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if (include_hidden or not d.startswith("."))
            and not (skip_libraries and d.lower().endswith(".photoslibrary"))
            and not _excluded(os.path.join(dirpath, d), exclude)
        )
        for name in filenames:
            walked += 1
            if not include_hidden and name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in ext_set:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            matched += 1
            yield FileInfo(full, st.st_size, st.st_mtime_ns)
        if on_progress is not None:
            on_progress(walked, matched)
