"""Tests for the optional CNN tier (torch-free: only the numpy fallback path)."""

from __future__ import annotations

import numpy as np

from cosuil.cnn import DeepTierUnavailableError, _find_pairs_numpy


def test_numpy_pair_finder():
    items = [
        ("a", np.array([1.0, 0.0], dtype=np.float32)),
        ("b", np.array([1.0, 0.05], dtype=np.float32)),
        ("c", np.array([0.0, 1.0], dtype=np.float32)),
    ]
    vecs = np.stack([v for _, v in items]).astype(np.float32)
    pairs = _find_pairs_numpy(items, vecs, threshold=0.9)
    names = {(p[0], p[1]) for p in pairs}
    assert ("a", "b") in names
    assert ("a", "c") not in names and ("b", "c") not in names


def test_numpy_pair_finder_empty():
    items = [("solo", np.array([1.0, 0.0], dtype=np.float32))]
    vecs = np.stack([v for _, v in items]).astype(np.float32)
    assert _find_pairs_numpy(items, vecs, threshold=0.9) == []


def test_deep_error_type_exists():
    assert issubclass(DeepTierUnavailableError, RuntimeError)
