# AGENTS.md

Instructions for any human or AI agent working in this repository.

## Summary

**cosúil** (Irish for *similar*) finds duplicate and similar photos in a
directory, shows them side by side in a local web UI so you can pick what to
keep, and moves the rest to the OS trash (recoverable). Scanning never modifies
files; only an explicit *Apply* does.

## Status

WIP, version 0.1.0. The scan, review UI and trash flow work and are covered by
tests. Known gaps:

- Applying decisions (moving files to the trash) is only available in the web
  UI and API; there is no CLI or TUI apply command.
- A group can never have every copy discarded — this is deliberate (see
  Project rules), not a bug.
- The optional deep (CNN) tier needs PyTorch and is not exercised by default.

## Commands

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # core + test deps (add ,cnn / ,avif as needed)
./install.sh                           # symlink `cosuil` into ~/bin (idempotent)

.venv/bin/python -m pytest             # full test suite (config in pyproject.toml)
.venv/bin/python -m pytest tests/test_apply_safety.py   # trash/delete safety tests

cosuil scan <dir>                      # scan a directory
cosuil serve                           # start the review web UI
cosuil scans                           # list saved scans
cosuil delete-scan --scan N            # delete a saved scan (files untouched)
cosuil paths                           # show data/cache/config/report locations
```

## Layout

| Path | What it is |
| --- | --- |
| `src/cosuil/cli.py` | Typer CLI entry point (`scan`, `serve`, `report`, `scans`, `delete-scan`, `paths`) |
| `src/cosuil/scanner.py` | Scan pipeline: discovery, hashing, grouping, persistence |
| `src/cosuil/discovery.py` | Directory walk and image-file filtering (symlinks skipped) |
| `src/cosuil/hashing.py`, `similarity.py`, `cnn.py` | Exact (BLAKE3), perceptual (phash/BK-tree) and optional CNN matching |
| `src/cosuil/quality.py`, `format.py` | Quality scoring and image-format handling |
| `src/cosuil/db.py` | SQLite schema, migrations and all queries |
| `src/cosuil/actions.py` | Applying decisions: the only code that moves files to the trash |
| `src/cosuil/thumbs.py` | Cached thumbnails and transcoded previews |
| `src/cosuil/config.py` | Scan config and data/cache/config/report directories |
| `src/cosuil/tui.py` | Rich terminal progress output |
| `src/cosuil/server/app.py` | FastAPI app and JSON API |
| `src/cosuil/server/static/` | Vanilla-JS single-page frontend (`index.html`, `app.js`, `styles.css`, `icon.svg`) |
| `tests/` | pytest suite; `conftest.py` generates the image fixture set |
| `scripts/perf_smoke.py` | Manual performance smoke test |
| `install.sh` | Symlinks the `cosuil` command into `$HOME/bin` |

## Technology Stack

### Python

- **Role**: the whole application.
- **Version**: `>=3.11` (`requires-python`); developed on 3.11.
- **Best Practices**:
  - Use `from __future__ import annotations` and type hints, as the existing modules do.
  - Keep dependencies few; prefer the standard library where it is enough.
- **Docs**: https://docs.python.org/3/

### FastAPI + Uvicorn

- **Role**: the local review server and its JSON API (`server/app.py`).
- **Version**: `fastapi>=0.115`, `uvicorn>=0.30`.
- **Best Practices**:
  - Endpoints are plain sync functions; shared state is guarded explicitly
    (`_RUNNING`, `_APPLY_LOCK`) because requests run in a thread pool.
  - Raise `HTTPException` with the right status (404 missing, 409 conflicting state).
- **Docs**: https://fastapi.tiangolo.com/ · https://www.uvicorn.org/

### Typer and Rich

- **Role**: the CLI (`typer`) and live terminal progress output (`rich`).
- **Version**: `typer>=0.12`, `rich>=13.7`.
- **Best Practices**:
  - Keep a plain-text fallback for non-TTY output.
- **Docs**: https://typer.tiangolo.com/ · https://rich.readthedocs.io/

### SQLite (standard library `sqlite3`)

- **Role**: persistence for scans, images, groups and decisions (`db.py`).
- **Version**: whatever ships with Python; WAL mode, foreign keys on.
- **Best Practices**:
  - All SQL lives in `db.py`; multi-statement changes run in one transaction
    with rollback on error.
  - Schema changes go through `_migrate`, gated by `PRAGMA user_version`.
  - Index any column used in a delete or join on a large table.
- **Docs**: https://docs.python.org/3/library/sqlite3.html

### send2trash

- **Role**: moves discarded files to the OS trash so they stay recoverable.
- **Version**: `>=1.8`.
- **Best Practices**:
  - Only `actions.py` may call it, and only after the safety checks there.
  - Tests replace it with a fake that really moves files; never let tests touch
    the real trash.
- **Docs**: https://github.com/arsenetar/send2trash

### Pillow, pillow-heif, imagehash, blake3, numpy

- **Role**: decoding images (incl. HEIC), perceptual hashing, exact byte hashing
  and numeric work for similarity and quality scoring.
- **Version**: `Pillow>=10.0`, `pillow-heif>=0.18`, `imagehash>=4.3`,
  `blake3>=0.4`, `numpy>=1.26`.
- **Best Practices**:
  - Treat every decode as fallible: record the error on the image, never crash the scan.
  - Reuse cached hashes for unchanged files (size + mtime) on rescans.
- **Docs**: https://pillow.readthedocs.io/ · https://github.com/bigbluehat/pillow-heif ·
  https://github.com/JohannesBuchner/imagehash · https://github.com/BLAKE3-team/BLAKE3

### PyTorch / torchvision (optional `cnn` extra)

- **Role**: CNN embeddings for the optional deep similarity tier.
- **Version**: `torch>=2.1`, `torchvision>=0.16`, with `numpy>=1.26,<2` because the
  last Intel-macOS torch wheels need numpy 1.x.
- **Best Practices**:
  - Import lazily so the core install works without it.
- **Docs**: https://pytorch.org/docs/stable/

### pytest + httpx (optional `dev` extra)

- **Role**: the test suite and the FastAPI `TestClient`.
- **Version**: `pytest>=8.0`, `httpx>=0.27`.
- **Best Practices**:
  - Isolate data/cache/config with the `tmp_dirs` fixture; never touch the user's real data.
  - New safety rules get a test that fails when the rule is disabled.
- **Docs**: https://docs.pytest.org/

### Vanilla JavaScript frontend

- **Role**: the review UI, served as static files by FastAPI; no build step.
- **Version**: none (browser-native ES2020+).
- **Best Practices**:
  - Escape every server-supplied string with `escapeHtml` before putting it in markup.
  - Syntax-check with `node --check src/cosuil/server/static/app.js` after editing.
- **Docs**: https://developer.mozilla.org/docs/Web/JavaScript

## Keeping `.gitignore` current — required

`.gitignore` is part of a change, never a follow-up to it. When a change creates
files that should not be committed, the patterns go in **the same commit** — a
cached thumbnail committed by accident is much harder to remove than a line is
to add.

Keep current, at minimum:

- **Tool and dependency output**: build directories, package caches, virtual
  environments, downloaded models and sample media, generated index files.
- **Local state**: config and env files (`*.env`, `.env.local`), log files, and
  anything naming this machine or a user's home directory.
- **Editor and OS metadata**: `.DS_Store`, `.vscode/`, `.idea/`, `*.swp`.

Rules:

- Prefer the official patterns published for the technology over invented ones;
  check the tool's own documentation rather than guessing.
- Group patterns by reason, each under a comment saying which tool or platform it
  belongs to, so the next reader can tell what a pattern is for.
- **Never** ignore editor tooling by reflex where the team may want it shared: an
  ignore rule is a commitment, and adding an un-ignore afterwards (`!.vscode/…`)
  is confusing. Ignore the specific local artefacts, not the whole directory, when
  that distinction matters.
- **Never commit secrets** — ignore the file that holds them. If one is committed
  by accident, say so in the commit that removes it rather than deleting it
  quietly, because the history still has it.
- The failure to watch for is the opposite of the usual one: ignoring a file the
  tool needs at run time. If it is needed to run or to build, it is source and it
  belongs in git.

## Project rules

These exist because cosuil deletes people's photos. Do not weaken them.

- **Only `actions.py` moves files.** Everything else is read-only with respect to
  the user's files. Deleting a scan removes database records only.
- **Trash only exactly what was reviewed.** `plan_apply` skips files that changed
  since the scan, are symlinks, lie outside the scan root, are missing, are
  discarded in one group but not another, or are the last surviving copy of a
  group. Add a case and a test before relaxing any of these.
- **Missing is not gone.** Never prune database records for missing files; an
  unmounted drive looks the same.
- **Always preview first.** The UI uses `POST /api/scans/{id}/apply?dry_run=true`
  before asking for confirmation; keep the preview and the real run on the same
  code path.
- **Keep the database in step with the disk.** After a trash run, trashed images
  are removed and groups are repaired (`remove_images`, `prune_scan`).
- **Deviation from the house rule on `static/`:** the frontend is a small set of
  separate files under `server/static/` rather than one embedded script. Keep it
  framework-free and build-free.

## Commit conventions

- Imperative sentences, no conventional-commit prefix: "Extract keyframes with a
  scene-change threshold".
- The subject says what changed; the body says **why**, and records the trap that
  was avoided or the measurement that justified the choice.
- One logical change per commit.

## AI attribution — required

Any commit produced with an AI coding assistant — for the code, the docs, the
artwork, or the commit message itself — must end with a trailer naming the
assistant **and** the exact model, its version, and the effort level it ran at:

```
Assisted-by: <assistant> (<model> <version>, effort: <level>)
```

```
Assisted-by: GitHub Copilot (DeepSeek V4 Flash, effort: high)
```

```
Assisted-by: Claude Code (Sonnet 5.5, effort: high)
```

Rules:

- Name the model you were actually running, and its version when it has one.
  "AI", "Copilot", "an LLM" or a bare tool name is not attribution.
- State the effort (reasoning) level the model was set to, as the tool names it
  (`low`, `medium`, `high`, …), because it changes how much to trust the work.
  If the tool has no such setting, leave the `effort:` part out; never guess.
- One trailer per assistant: two assistants on one commit means two
  `Assisted-by:` trailers.
- Use `git commit --trailer "Assisted-by=GitHub Copilot (DeepSeek V4 Flash, effort: high)"`
  rather than hand-placing the line, so it lands in the trailer block.
- No trailer means the work was written by hand. Never add one for a change you
  did not make.

### Elsewhere, not only in commits

Git history is not the only place this is read, so the same attribution applies
wherever the work is visible without it:

- **Documents and prose** substantially written by an assistant — this file,
  `README.md`, design notes — carry the assistant, model, version and effort level, either as a
  note in the document or in a short "AI contributions" list.
- **Release notes, changelogs, tags and pull-request descriptions** written with
  an assistant end with the same `Assisted-by:` trailer.
- **Generated artefacts** that are committed (thumbnails, classification output,
  fixtures) record in their header or in the command that regenerates them which
  tooling and which assistant produced them, so the result can be reproduced.

When in doubt, attribute. An unnecessary trailer costs a line; a missing one
misrepresents who wrote the work.

## General house rules

- **License**: every repository ships a `LICENSE`. Default to MIT. Use
  AGPL-3.0-or-later when the project links or invokes GPL-licensed tooling (for
  example a `--enable-gpl` FFmpeg build), or when the work should stay reusable by
  AGPL projects.
- **README**: every repository has a README that says what the project is, how to
  install it, and how to use it.
- **Install**: command-line tools install via an idempotent `install.sh` that
  symlinks into `$HOME/bin`, and warn when `$HOME/bin` is not on `PATH` (zsh).
- **Prefer self-contained, low-dependency tooling**: plain stdlib/Python or vanilla
  web tech over frameworks unless a dependency earns its place, and single-file
  scripts with embedded frontend assets over separate `static/` folders.
- **Verify before claiming done**: run the build, tests, linters, or a dry run and
  record the outcome in the commit body.
- **Keep this file current**: when a change introduces a language, library, or
  dependency, update `AGENTS.md` in the same commit — with rationale, a Best
  Practices block, and docs links — and add the matching `.gitignore` patterns in
  that same commit.

## AI contributions

This file was written by an assistant: Claude Code (Sonnet 5.5, effort: high).
