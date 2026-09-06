"""Similarity grouping: BK-tree pair finding + union-find group construction."""

from __future__ import annotations

from typing import Iterable, Iterator, Sequence

from .hashing import hamming


class BKTree:
    """Burkhard-Keller tree over 64-bit hashes with Hamming distance.

    Supports incremental insertion and radius queries in O(log n) on average,
    which keeps near-duplicate pairing feasible at the 100k-image scale.
    """

    __slots__ = ("idx", "hash", "children")

    def __init__(self, idx: int, hash_value: int):
        self.idx = idx
        self.hash = hash_value
        self.children: dict[int, BKTree] = {}

    def add(self, idx: int, hash_value: int) -> None:
        node = self
        while True:
            d = hamming(hash_value, node.hash)
            if d == 0:
                return  # identical hash already present; pairs handled via index elsewhere
            child = node.children.get(d)
            if child is None:
                node.children[d] = BKTree(idx, hash_value)
                return
            node = child

    def query(self, hash_value: int, threshold: int) -> Iterator[tuple[int, int]]:
        """Yield (idx, distance) for hashes within *threshold* of *hash_value*."""
        stack: list[BKTree] = [self]
        while stack:
            node = stack.pop()
            d = hamming(hash_value, node.hash)
            if d <= threshold:
                yield node.idx, d
            low, high = d - threshold, d + threshold
            for dist, child in node.children.items():
                if low <= dist <= high:
                    stack.append(child)


def find_near_pairs(
    items: Sequence[tuple[int, int]], threshold: int
) -> Iterator[tuple[int, int, int]]:
    """Yield (idx_a, idx_b, distance) pairs from *items* of (id, hash).

    Pairs are yielded once, with idx_a < idx_b in insertion order.
    """
    tree: BKTree | None = None
    for idx, hash_value in items:
        if tree is None:
            tree = BKTree(idx, hash_value)
            continue
        yield from ((other, idx, d) for other, d in tree.query(hash_value, threshold))
        tree.add(idx, hash_value)


class UnionFind:
    """Disjoint-set with path compression and union by size."""

    __slots__ = ("parent", "size")

    def __init__(self, n: int):
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        parent = self.parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


def group_pairs(pairs: Iterable[tuple[int, int]], n: int) -> list[list[int]]:
    """Merge overlapping pairs into transitive groups; each group is sorted."""
    uf = UnionFind(n)
    for a, b in pairs:
        uf.union(a, b)
    buckets: dict[int, list[int]] = {}
    for node in range(n):
        root = uf.find(node)
        if root != node:
            buckets.setdefault(root, []).append(node)
    groups: list[list[int]] = []
    for root, members in buckets.items():
        groups.append(sorted([root, *members]))
    return groups
