"""
Biometric Face Embedding Comparator.
Computes normalized cosine similarity between 128-dimensional face representations.
"""

import math
from typing import Sequence


def compute_cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """
    Compute cosine similarity between two feature vectors in [-1.0, 1.0].
    Zero division safe.
    """
    if len(vec_a) != len(vec_b) or not vec_a:
        raise ValueError(f"Embedding length mismatch: {len(vec_a)} vs {len(vec_b)}.")

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    similarity = dot_product / (norm_a * norm_b)
    # Clamp due to floating point precision anomalies
    return max(-1.0, min(1.0, similarity))


def generate_synthetic_embedding(seed: int, dimension: int = 128) -> list[float]:
    """Generate a deterministic normalized 128-dim embedding for testing."""
    import random

    rng = random.Random(seed)  # noqa: S311 (synthetic benchmark only)
    raw = [rng.gauss(0.0, 1.0) for _ in range(dimension)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]
