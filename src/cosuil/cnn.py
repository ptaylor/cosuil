"""Optional CNN tier: imagededup embeddings + hnswlib approximate nearest neighbours.

This tier is only usable when the `[cnn]` extra is installed
(`pip install 'cosuil[cnn]'`). It is off by default and runs only on images not
already grouped by the exact/perceptual tiers.
"""

from __future__ import annotations

import numpy as np


class DeepTierUnavailableError(RuntimeError):
    """Raised when the optional CNN dependencies are not installed."""


def compute_embeddings(paths: list[str]) -> dict[str, np.ndarray]:
    try:
        from imagededup.methods import CNN
    except ImportError as exc:  # pragma: no cover
        raise DeepTierUnavailableError(
            "CNN tier requires the optional extra: pip install 'cosuil[cnn]' "
            "(imagededup, torch, torchvision, hnswlib)"
        ) from exc

    try:
        cnn = CNN(verbose=False)
    except TypeError:  # older/newer constructor signatures differ
        cnn = CNN()

    encodings: dict[str, np.ndarray] = {}
    for path in paths:
        try:
            enc = cnn.encode_image(image_file=path)
        except Exception:
            continue
        if enc is not None:
            encodings[path] = np.asarray(enc, dtype=np.float32).ravel()
    return encodings


def find_pairs(paths: list[str], threshold: float = 0.85) -> list[tuple[str, str, float]]:
    """Return (path_a, path_b, cosine_similarity) pairs at or above *threshold*."""
    try:
        import hnswlib
    except ImportError as exc:  # pragma: no cover
        raise DeepTierUnavailableError(
            "CNN tier requires hnswlib: pip install hnswlib"
        ) from exc

    encodings = compute_embeddings(paths)
    if len(encodings) < 2:
        return []
    items = list(encodings.items())
    vecs = np.stack([v for _, v in items]).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs /= norms  # cosine similarity == inner product on unit vectors

    index = hnswlib.Index(space="ip", dim=vecs.shape[1])
    index.init_index(max_elements=len(items), ef_construction=200, M=16)
    index.add_items(vecs)
    index.set_ef(200)

    pairs: list[tuple[str, str, float]] = []
    seen: set[tuple[int, int]] = set()
    for i, (path, _) in enumerate(items):
        labels, dists = index.knn_query(vecs[i], k=min(len(items), 50))
        for label, dist in zip(labels[0], dists[0]):
            j = int(label)
            a, b = (i, j) if i < j else (j, i)
            if a == b or (a, b) in seen:
                continue
            seen.add((a, b))
            if float(dist) >= threshold:
                pairs.append((items[a][0], items[b][0], float(dist)))
    return pairs
