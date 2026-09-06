# cosúil

**cosúil** (Irish for *similar*) finds duplicate and similar photos in a
directory, then gives you a visual side-by-side review UI to decide which
copies to keep — weighing image quality, file size, last-modified time and
EXIF data. Files you don't keep are moved to the OS trash (recoverable).

## Features

- **Hybrid detection tiers**
  - *Exact* — BLAKE3 byte hashes catch true duplicates (size-prefiltered)
  - *Similar* — perceptual hashes (phash) with BK-tree pairing for near-duplicates
    (resized, recompressed, watermarked…)
  - *Deep (optional)* — CNN embeddings (`imagededup` + `hnswlib`) for visually
    similar images beyond phash reach
- **Broad format support** (case-insensitive, all extension variants):
  GIF · JPEG (`.jpg .jpeg .jpe .jfif`) · PNG · WebP · HEIC/HEIF (`.heic .heif .hif`)
  · Netpbm family (`.pbm .pgm .ppm .pnm .pam`) · XBM/XPM · best-effort
  AVIF/BMP/TIFF
- **Beautiful live terminal output** — Docker-style colored progress with
  counters, progress bar and a rolling activity window (Rich), plus a plain
  fallback for non-TTY/CI usage
- **Visual review web UI** — thumbnail group grid, side-by-side comparison with
  synchronized zoom, blink compare, per-image quality score and metadata
  badges, keyboard-driven keep/discard decisions, auto-suggest
- **Safe by default** — scanning never touches files; only an explicit *Apply*
  moves discarded images to the OS trash, and a JSON report records every move
- **Incremental rescans** — unchanged files reuse cached hashes (10–100× faster
  re-runs); thumbnails and transcoded previews are cached on disk

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e .            # core
.venv/bin/pip install -e ".[cnn]"     # optional deep tier (PyTorch, ~2 GB)
.venv/bin/pip install -e ".[avif]"    # optional AVIF support
```

## Usage

```bash
cosuil scan ~/Pictures                # live progress + summary
cosuil scan ~/Pictures --kind all     # add the CNN deep tier
cosuil scan ~/Pictures -t 8           # looser similarity threshold (0–64)
cosuil scan ~/Pictures --quiet        # plain output (pipes/CI)
cosuil report                         # latest scan: groups + reclaimable bytes
cosuil scans                          # list saved scans
cosuil delete-scan --scan 3           # delete a saved scan (files untouched)
cosuil serve                          # start review UI → http://127.0.0.1:8787
```

### Review workflow

1. `cosuil scan DIR` and note the summary table.
2. `cosuil serve` opens the review UI.
3. The group grid shows thumbnail cards (kind badge, member count, reclaimable
   bytes). Click a card for the side-by-side compare view.
4. For each group decide keepers with the keyboard:
   - `1–9` select an image, `←/→` move, `K` keep, `X` discard
   - `Z` zoom (synchronized across previews), `B` blink compare
   - `Enter` save & next group, `S` save & stay, `↑/↓` navigate groups
   - *Auto-suggest* marks the highest-scoring image as keep
   - Each photo shows its folder (color-coded). Clicking a folder chip keeps
     everything from that folder and discards the rest; marking one photo as
     *keep* auto-keeps its folder-mates too.
5. Use the **Apply** bar to move everything marked *discard* to the OS trash.
   A JSON report with every moved path is written to the data directory, and
   files remain recoverable from the trash.

### Quality score

Each image gets a 0–100 score from configurable factors:
resolution (12 MP = full marks) · sharpness (Laplacian variance) · file size ·
format (lossless > lossy) · EXIF (camera + capture date present).

## Configuration

Optional `~/.config/cosuil/config.toml`:

```toml
[scan]
phash_threshold = 6
cnn_threshold = 0.85
thumb_size = 256
include_hidden = false
extensions = [".gif", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".ppm", ".pgm"]

[quality]
resolution = 0.30
sharpness = 0.25
size = 0.15
format = 0.10
exif = 0.20
```

## Data locations

| What | Where |
| --- | --- |
| Scan database (SQLite) | `~/.local/share/cosuil/cosuil.db` (or `$COSUIL_DATA_DIR`) |
| Apply reports (JSON) | `~/.local/share/cosuil/reports/` |
| Thumbnail / preview cache | `~/.cache/cosuil/thumbnails/` (or `$COSUIL_CACHE_DIR`) |

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
python scripts/perf_smoke.py 2000     # synthetic perf smoke test
```

## Notes

- "Google's format" = WebP; Apple HEIC/HEIF included via `pillow-heif`.
- The CNN deep tier requires the `[cnn]` extra (torch + torchvision, prebuilt
  wheels — no C++ compiler needed) and runs only on images the other tiers did
  not group. It embeds images with torchvision's MobileNetV3; `hnswlib` is used
  automatically as an accelerator when installed.
- Exact copies inside a similar group are badged *exact copy* in the UI.
- The deep tier needs PyTorch. On **Intel (x86_64) macOS**, PyTorch wheels stop
  at 2.2.x and require **Python ≤ 3.12** — use such a venv for `[cnn]` there.
  (Apple Silicon and Linux have no such limit.)
