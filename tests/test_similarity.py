"""Unit tests for BK-tree pair finding and union-find grouping."""

from __future__ import annotations

import random

from cosuil.similarity import BKTree, find_near_pairs, group_pairs


def _bruteforce_pairs(items, threshold):
    pairs = set()
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if (items[i][1] ^ items[j][1]).bit_count() <= threshold:
                pairs.add((i, j))
    return pairs


def test_bktree_matches_bruteforce():
    rng = random.Random(42)
    hashes = [(i, rng.getrandbits(64)) for i in range(500)]
    threshold = 6
    got = {
        (a, b)
        for a, b, _d in find_near_pairs(hashes, threshold)
    }
    expected = _bruteforce_pairs(hashes, threshold)
    assert got == expected


def test_bktree_small_threshold():
    items = [(0, 0b0000), (1, 0b0001), (2, 0b0111), (3, 0b1111)]
    got = {(a, b) for a, b, _d in find_near_pairs(items, threshold=1)}
    # 0b0111 and 0b1111 differ in exactly one bit too
    assert got == {(0, 1), (2, 3)}


def test_group_pairs_transitive_closure():
    # 0-1 and 1-2 connected => one group of three; 3-4 another
    groups = group_pairs([(0, 1), (1, 2), (3, 4)], n=5)
    assert sorted(groups) == [[0, 1, 2], [3, 4]]


def test_group_pairs_no_pairs():
    assert group_pairs([], n=4) == []


def test_group_pairs_order_independent():
    a = group_pairs([(2, 0), (1, 2)], n=3)
    b = group_pairs([(0, 1), (0, 2)], n=3)
    assert sorted(a) == sorted(b) == [[0, 1, 2]]
