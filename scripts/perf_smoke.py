#!/usr/bin/env python3
"""Performance smoke test: generate N synthetic images and time a full scan.

Usage:
    python scripts/perf_smoke.py [COUNT] [--workers W]

The generated set contains exact copies, recompressed variants and distinct
images so every tier does real work.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def generate(count: int, dest: Path) -> None:
    import random

    from PIL import Image, ImageDraw

    dest.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1234)
    base = Image.new("RGB", (256, 256))
    draw = ImageDraw.Draw(base)
    for _ in range(40):
        x0, x1 = sorted([rng.randint(0, 200), rng.randint(56, 256)])
        y0, y1 = sorted([rng.randint(0, 200), rng.randint(56, 256)])
        draw.ellipse(
            [x0, y0, x1, y1],
            fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)),
        )

    i = 0
    while i < count:
        img = base.copy()
        name = f"img{i:05d}"
        img.save(dest / f"{name}.jpg", "JPEG", quality=90)
        i += 1
        if i < count:  # exact byte copy
            shutil.copyfile(dest / f"{name}.jpg", dest / f"{name}-copy.jpeg")
            i += 1
        if i < count:  # recompressed near-duplicate
            img.save(dest / f"{name}-low.jpg", "JPEG", quality=50)
            i += 1
        if i < count and i % 5 == 0:  # distinct image
            Image.new("RGB", (256, 256),
                      (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
                      ).save(dest / f"distinct{i:05d}.jpg", "JPEG", quality=90)
            i += 1


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    workers = 0
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])

    from cosuil.config import DEFAULT_EXTENSIONS, ScanConfig
    from cosuil.db import Database
    from cosuil.scanner import Scanner

    dest = Path("/tmp/cosuil-perf/pics")
    shutil.rmtree(dest, ignore_errors=True)
    print(f"generating {count} synthetic images…")
    t0 = time.monotonic()
    generate(count, dest)
    print(f"generated in {time.monotonic() - t0:.1f}s")

    cfg = ScanConfig.from_toml(
        root=dest,
        kinds=("exact", "similar"),
        phash_threshold=6,
        cnn_threshold=0.85,
        include_hidden=False,
        extensions=DEFAULT_EXTENSIONS,
        workers=workers,
        thumb_size=256,
        fresh=True,
    )
    db = Database(Path("/tmp/cosuil-perf/data/cosuil.db"))
    t0 = time.monotonic()
    result = Scanner(db, cfg).run()
    elapsed = time.monotonic() - t0
    rate = result["images_found"] / elapsed if elapsed else 0.0
    print(
        f"scanned {result['images_found']} images in {elapsed:.1f}s "
        f"({rate:.0f} img/s) → {result['exact_groups']} exact, "
        f"{result['similar_groups']} similar groups, {result['errors']} errors"
    )


if __name__ == "__main__":
    main()
