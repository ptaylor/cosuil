"""Optional CNN tier: torchvision MobileNetV3 embeddings + cosine similarity.

Requires the `[cnn]` extra (`torch` + `torchvision` — both install from
prebuilt wheels, no C++ compiler needed). `hnswlib` is used automatically as an
accelerator when installed; otherwise a chunked numpy fallback computes
cosine similarities, which is perfectly adequate for the leftover images the
deep tier operates on.

This tier is off by default and runs only on images not already grouped by the
exact/perceptual tiers.
"""

from __future__ import annotations

import numpy as np

_IMAGENET_MEAN = [0.485, 0.456, 0.406]
_IMAGENET_STD = [0.229, 0.224, 0.225]


class DeepTierUnavailableError(RuntimeError):
    """Raised when the optional CNN dependencies are not installed."""


def _load_model():
    import torch
    from torchvision import models, transforms

    weights = models.MobileNet_V3_Large_Weights.IMAGENET1K_V1
    model = models.mobilenet_v3_large(weights=weights)
    model.classifier = torch.nn.Identity()  # keep the 960-d feature vector
    model.eval()
    transform = transforms.Compose(
        [
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=_IMAGENET_MEAN, std=_IMAGENET_STD),
        ]
    )
    return model, transform


def compute_embeddings(paths: list[str]) -> dict[str, np.ndarray]:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise DeepTierUnavailableError(
            "CNN tier requires the optional extra: pip install 'cosuil[cnn]' "
            "(torch, torchvision)"
        ) from exc
    from PIL import Image, ImageOps

    model, transform = _load_model()
    encodings: dict[str, np.ndarray] = {}
    with torch.no_grad():
        for path in paths:
            try:
                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im)
                    tensor = transform(im.convert("RGB")).unsqueeze(0)
                    # .tolist() rather than .numpy(): torch 2.2.x is not
                    # compatible with numpy 2.x's array API bridge.
                    vec = model(tensor).squeeze(0).cpu().tolist()
                encodings[path] = np.asarray(vec, dtype=np.float32)
            except Exception:
                continue
    return encodings


def find_pairs(paths: list[str], threshold: float = 0.85) -> list[tuple[str, str, float]]:
    """Return (path_a, path_b, cosine_similarity) pairs at or above *threshold*."""
    encodings = compute_embeddings(paths)
    if len(encodings) < 2:
        return []
    items = list(encodings.items())
    vecs = np.stack([v for _, v in items]).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs /= norms  # cosine similarity == inner product on unit vectors

    try:
        import hnswlib  # noqa: F401
    except ImportError:
        return _find_pairs_numpy(items, vecs, threshold)
    try:
        return _find_pairs_hnsw(items, vecs, threshold)
    except Exception:
        return _find_pairs_numpy(items, vecs, threshold)


def _find_pairs_hnsw(
    items: list[tuple[str, np.ndarray]], vecs: np.ndarray, threshold: float
) -> list[tuple[str, str, float]]:
    import hnswlib

    index = hnswlib.Index(space="ip", dim=vecs.shape[1])
    index.init_index(max_elements=len(items), ef_construction=200, M=16)
    index.add_items(vecs)
    index.set_ef(200)

    pairs: list[tuple[str, str, float]] = []
    seen: set[tuple[int, int]] = set()
    for i in range(len(items)):
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


def _find_pairs_numpy(
    items: list[tuple[str, np.ndarray]], vecs: np.ndarray, threshold: float
) -> list[tuple[str, str, float]]:
    """Blocked matrix multiply over unit vectors; O(n^2) pairs but vectorized."""
    pairs: list[tuple[str, str, float]] = []
    n = len(vecs)
    chunk = 512
    for i in range(0, n, chunk):
        block_a = vecs[i : i + chunk]
        for j in range(i, n, chunk):
            sims = block_a @ vecs[j : j + chunk].T
            rows, cols = np.where(sims >= threshold)
            for r, c in zip(rows, cols):
                a, b = i + int(r), j + int(c)
                if a >= b:
                    continue
                pairs.append((items[a][0], items[b][0], float(sims[r, c])))
    return pairs
