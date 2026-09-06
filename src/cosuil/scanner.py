"""Scan orchestration: discovery → exact tier → perceptual tier → optional CNN tier."""

from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Callable, Sequence

from .config import ScanConfig
from .db import Database
from .discovery import FileInfo, iter_image_files
from .hashing import blake3_file, phash_image, phash_int, register_extra_openers
from .similarity import find_near_pairs, group_pairs


@dataclass
class ScanCounters:
    stage: str = "starting"
    files_walked: int = 0
    images_found: int = 0
    images_hashed: int = 0
    hashes_reused: int = 0
    exact_groups: int = 0
    similar_groups: int = 0
    deep_groups: int = 0
    errors: int = 0
    elapsed: float = 0.0
    message: str = ""


ProgressFn = Callable[[ScanCounters], None]
LogFn = Callable[[str], None]


def _exact_worker(item: tuple[int, str]) -> tuple[int, str | None]:
    """Process-pool worker: BLAKE3 hash of one file (module-level for pickling)."""
    image_id, path = item
    try:
        return image_id, blake3_file(path)
    except Exception:
        return image_id, None


def _phash_worker(item: tuple[int, str]):
    """Process-pool worker: phash + quality metadata of one file."""
    image_id, path = item
    return image_id, phash_image(path)


def _chunks(items: Sequence, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class Scanner:
    """Runs a scan, reporting progress through callbacks and the database.

    on_progress receives a fresh ScanCounters snapshot; on_log receives human
    readable activity lines. Database progress is updated at most every 0.5s so
    the web UI can poll cheaply.
    """

    def __init__(
        self,
        db: Database,
        cfg: ScanConfig,
        on_progress: ProgressFn | None = None,
        on_log: LogFn | None = None,
    ):
        self.db = db
        self.cfg = cfg
        self.on_progress = on_progress
        self.on_log = on_log
        self.counters = ScanCounters()
        self._start = time.monotonic()
        self._last_db_write = 0.0
        self._last_log = 0.0

    # -- reporting -----------------------------------------------------------
    def _log(self, line: str, throttle: float = 0.0) -> None:
        if self.on_log is None:
            return
        now = time.monotonic()
        if throttle and now - self._last_log < throttle:
            return
        self._last_log = now
        self.on_log(line)

    def _emit(self, force: bool = False) -> None:
        self.counters.elapsed = round(time.monotonic() - self._start, 2)
        if self.on_progress is not None:
            self.on_progress(
                ScanCounters(
                    stage=self.counters.stage,
                    files_walked=self.counters.files_walked,
                    images_found=self.counters.images_found,
                    images_hashed=self.counters.images_hashed,
                    hashes_reused=self.counters.hashes_reused,
                    exact_groups=self.counters.exact_groups,
                    similar_groups=self.counters.similar_groups,
                    deep_groups=self.counters.deep_groups,
                    errors=self.counters.errors,
                    elapsed=self.counters.elapsed,
                    message=self.counters.message,
                )
            )
        now = time.monotonic()
        if force or now - self._last_db_write >= 0.5:
            self._last_db_write = now
            self.db.update_scan(
                self.scan_id,
                status="running",
                files_walked=self.counters.files_walked,
                images_found=self.counters.images_found,
                images_hashed=self.counters.images_hashed,
                exact_groups=self.counters.exact_groups,
                similar_groups=self.counters.similar_groups,
                deep_groups=self.counters.deep_groups,
            )

    def _parallel(
        self,
        func: Callable,
        items: Sequence,
        on_item: Callable | None = None,
        log_every: int = 0,
        log_prefix: str = "",
    ) -> list:
        """Map *func* over *items* in a process pool, order-preserving."""
        workers = self.cfg.workers or max(1, (os.cpu_count() or 4))
        if not items:
            return []
        done = 0
        if workers == 1 or len(items) <= 32:
            out = []
            for item in items:
                out.append(func(item))
                done += 1
                if on_item is not None:
                    on_item()
                if log_every and done % log_every == 0:
                    self._log(f"{log_prefix} {done}/{len(items)}")
            return out
        with ProcessPoolExecutor(
            max_workers=workers, initializer=register_extra_openers
        ) as pool:
            out = []
            for result in pool.map(
                func, items, chunksize=max(1, len(items) // (workers * 8))
            ):
                out.append(result)
                done += 1
                if on_item is not None:
                    on_item()
                if log_every and done % log_every == 0:
                    self._log(f"{log_prefix} {done}/{len(items)}")
        return out

    # -- main entry ------------------------------------------------------------
    def run(self, scan_id: int | None = None) -> dict:
        db, cfg = self.db, self.cfg
        kinds = tuple(k for k in cfg.kinds if k in ("exact", "similar", "deep"))
        self.scan_id = scan_id if scan_id is not None else db.create_scan(
            str(cfg.root), cfg.to_json()
        )
        try:
            self._run(kinds)
        except Exception as exc:  # pragma: no cover - defensive
            db.update_scan(
                self.scan_id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            raise
        return self._finish()

    def _run(self, kinds: tuple[str, ...]) -> None:
        db, cfg = self.db, self.cfg
        c = self.counters

        # ---- discovery -----------------------------------------------------
        c.stage = "discovering"
        c.message = "walking directory tree"
        files: list[FileInfo] = []
        walked = 0

        def on_walk(total_walked: int, _matched: int) -> None:
            nonlocal walked
            walked = total_walked

        for fi in iter_image_files(
            cfg.root,
            cfg.extensions,
            cfg.include_hidden,
            skip_libraries=cfg.skip_libraries,
            on_progress=on_walk,
        ):
            files.append(fi)
            if len(files) % 500 == 0:
                c.files_walked = walked
                c.images_found = len(files)
                self._emit()
        c.files_walked = walked
        c.images_found = len(files)
        c.message = f"found {len(files)} image files"
        self._log(c.message)
        self._emit(force=True)

        if not files:
            c.stage = "done"
            self._emit(force=True)
            return

        db.insert_image_files(self.scan_id, files)
        rows = db.get_image_rows(self.scan_id)  # (id, path, size, mtime_ns)
        path_of = {r["id"]: r["path"] for r in rows}
        c.message = f"indexed {len(rows)} files"
        self._emit(force=True)

        # ---- hash reuse from previous scan -----------------------------------
        reused = {} if cfg.fresh else db.previous_hashes(str(cfg.root))

        exact_groups: list[list[int]] = []
        similar_groups: list[list[int]] = []
        deep_groups: list[list[int]] = []
        phash_map: dict[int, int] = {}
        b3_map: dict[int, str] = {}
        updates: list[tuple[int, dict]] = []
        errors = 0

        # ---- tier 1: exact duplicates -----------------------------------------
        if "exact" in kinds:
            c.stage = "exact"
            c.message = "hashing bytes for exact duplicates"
            self._emit()
            from collections import defaultdict

            by_size: dict[int, list] = defaultdict(list)
            for row in rows:
                by_size[row["size"]].append(row)

            work: list[tuple[int, str]] = []
            known: dict[int, str] = {}
            for size, members in by_size.items():
                if len(members) < 2:
                    continue
                for row in members:
                    key = (row["path"], row["size"], row["mtime_ns"])
                    prev = reused.get(key, {}).get("blake3")
                    if prev:
                        known[row["id"]] = prev
                        c.hashes_reused += 1
                    else:
                        work.append((row["id"], row["path"]))

            for image_id, digest in self._parallel(
                _exact_worker, work, log_every=200, log_prefix="exact-hashed"
            ):
                if digest is None:
                    errors += 1
                    self._log(f"unreadable: {path_of.get(image_id, '?')}", throttle=0.05)
                    continue
                b3_map[image_id] = digest

            by_digest: dict[str, list[int]] = defaultdict(list)
            for row in rows:
                digest = b3_map.get(row["id"]) or known.get(row["id"])
                if digest:
                    by_digest[digest].append(row["id"])
                    updates.append((row["id"], {"blake3": digest}))
            exact_groups = [sorted(ids) for ids in by_digest.values() if len(ids) > 1]
            c.exact_groups = len(exact_groups)
            c.message = f"{len(exact_groups)} exact duplicate groups"
            self._log(c.message)
            self._emit()

        # ---- tier 2: perceptual similarity --------------------------------------
        if "similar" in kinds:
            c.stage = "hashing"
            c.message = "computing perceptual hashes"
            self._emit()
            work: list[tuple[int, str]] = []
            for row in rows:
                key = (row["path"], row["size"], row["mtime_ns"])
                prev = reused.get(key, {})
                if prev and prev.get("phash"):
                    try:
                        phash_map[row["id"]] = int(prev["phash"], 16)
                    except (ValueError, TypeError):
                        pass
                    else:
                        c.hashes_reused += 1
                        updates.append(
                            (
                                row["id"],
                                {
                                    k: prev[k]
                                    for k in (
                                        "width", "height", "format", "sharpness",
                                        "exif_json", "quality_score", "quality_json",
                                    )
                                },
                            )
                        )
                        continue
                work.append((row["id"], row["path"]))

            def _ph_done() -> None:
                c.images_hashed += 1
                if c.images_hashed % 25 == 0:
                    self._emit()

            for image_id, info in self._parallel(
                _phash_worker, work, on_item=_ph_done, log_every=500, log_prefix="hashed"
            ):
                fields = {
                    "phash": info.phash,
                    "width": info.width,
                    "height": info.height,
                    "format": info.format,
                    "sharpness": info.sharpness,
                    "exif_json": _json(info.exif),
                    "quality_score": info.quality_score,
                    "quality_json": _json(info.quality_factors),
                    "error": info.error,
                }
                updates.append((image_id, fields))
                if info.error:
                    errors += 1
                    self._log(f"unreadable image: {info.path} — {info.error}", throttle=0.05)
                ph = phash_int(info)
                if ph is not None:
                    phash_map[image_id] = ph
            c.errors = errors

            c.stage = "grouping"
            c.message = "grouping similar images"
            self._emit()
            items = sorted(phash_map.items(), key=lambda t: t[0])
            seq = [(i, h) for i, (_iid, h) in enumerate(items)]
            pairs = [
                (a, b) for a, b, _d in find_near_pairs(seq, cfg.phash_threshold)
            ]
            raw_groups = [
                [items[pos][0] for pos in g] for g in group_pairs(pairs, len(items))
            ]

            similar_groups, exact_groups = self._reconcile(raw_groups, exact_groups)
            c.similar_groups = len(similar_groups)
            c.exact_groups = len(exact_groups)
            c.message = f"{len(similar_groups)} similar groups"
            self._log(c.message)
            self._emit()

        # ---- tier 3: optional CNN ----------------------------------------------
        if "deep" in kinds:
            c.stage = "deep"
            c.message = "CNN embeddings for visual similarity"
            self._log(c.message)
            self._emit()
            deep_groups = self._deep_tier(rows, exact_groups, similar_groups)
            c.deep_groups = len(deep_groups)
            c.message = f"{len(deep_groups)} deep groups"
            self._log(c.message)
            self._emit()

        # ---- persist groups ------------------------------------------------------
        db.update_images_many(updates)
        quality_of: dict[int, float] = {
            r["id"]: r["quality_score"] or 0.0
            for r in db.get_image_rows(self.scan_id)
        }
        for kind, groups in (
            ("exact", exact_groups),
            ("similar", similar_groups),
            ("deep", deep_groups),
        ):
            for g in groups:
                rep = max(g, key=lambda iid: quality_of.get(iid, 0.0))
                db.insert_group(self.scan_id, kind, g, rep_image_id=rep)

        c.stage = "done"
        self._emit(force=True)

    def _reconcile(
        self, similar: list[list[int]], exact: list[list[int]]
    ) -> tuple[list[list[int]], list[list[int]]]:
        """Return (similar_groups, exact_groups) with no membership overlap.

        Exact groups fully contained in a similar group are absorbed into it;
        similar groups identical to an exact group add no information and are
        dropped. Every image ends up in at most one persisted group.
        """
        exact_sets = [set(g) for g in exact]
        covered = [False] * len(exact_sets)
        kept: list[list[int]] = []
        for group in similar:
            gs = set(group)
            contained = [gi for gi, s in enumerate(exact_sets) if s <= gs]
            if contained and len(contained) == 1 and exact_sets[contained[0]] == gs:
                continue  # redundant with the exact group
            for gi in contained:
                covered[gi] = True
            kept.append(group)
        remaining_exact = [
            sorted(s) for gi, s in enumerate(exact_sets) if not covered[gi]
        ]
        return kept, remaining_exact

    def _deep_tier(
        self,
        rows: Sequence,
        exact_groups: list[list[int]],
        similar_groups: list[list[int]],
    ) -> list[list[int]]:
        """CNN tier over images not already in any group. Returns groups of image ids."""
        try:
            from .cnn import find_pairs  # deferred heavy import
        except Exception as exc:
            self._log(f"deep tier unavailable: {exc}")
            return []

        grouped: set[int] = set()
        for g in exact_groups:
            grouped.update(g)
        for g in similar_groups:
            grouped.update(g)
        candidates = [dict(r) for r in rows if r["id"] not in grouped]
        if len(candidates) < 2:
            return []

        paths = [r["path"] for r in candidates]
        id_of = {r["path"]: r["id"] for r in candidates}
        try:
            pairs = find_pairs(paths, threshold=self.cfg.cnn_threshold)
        except Exception as exc:
            self._log(f"deep tier failed: {exc}")
            return []

        nodes = sorted({id_of[p] for pair in pairs for p in pair[:2]})
        index = {iid: i for i, iid in enumerate(nodes)}
        groups = group_pairs(
            [(index[id_of[a]], index[id_of[b]]) for a, b, _ in pairs], len(nodes)
        )
        return [sorted(nodes[i] for i in g) for g in groups if len(g) > 1]

    def _finish(self) -> dict:
        self.counters.elapsed = round(time.monotonic() - self._start, 2)
        self.db.update_scan(
            self.scan_id,
            status="done",
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            files_walked=self.counters.files_walked,
            images_found=self.counters.images_found,
            images_hashed=self.counters.images_hashed,
            exact_groups=self.counters.exact_groups,
            similar_groups=self.counters.similar_groups,
            deep_groups=self.counters.deep_groups,
        )
        return {
            "scan_id": self.scan_id,
            "files_walked": self.counters.files_walked,
            "images_found": self.counters.images_found,
            "images_hashed": self.counters.images_hashed,
            "hashes_reused": self.counters.hashes_reused,
            "exact_groups": self.counters.exact_groups,
            "similar_groups": self.counters.similar_groups,
            "deep_groups": self.counters.deep_groups,
            "errors": self.counters.errors,
            "elapsed": self.counters.elapsed,
        }


def _json(value) -> str | None:
    if value is None:
        return None
    import json

    return json.dumps(value)
