"""Reciprocal Rank Fusion (RRF).

RRF combines several ranked lists into one by summing ``1 / (k + rank)`` over
every list a document appears in. It fuses systems whose scores live on
different scales — dense cosine similarity and BM25 term-frequency scores — using
*ranks only*, so no per-system score normalisation or tuning is required
(technical-spec §3.4, model-definition C4). The default ``k = 60`` follows the
original Cormack et al. (2009) formulation and the spec.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence


def rrf(rankings: Sequence[Sequence[Hashable]], k: int = 60) -> list[tuple[Hashable, float]]:
    """Fuse ranked id lists into a single ranking via Reciprocal Rank Fusion.

    Args:
        rankings: One ranked list per retrieval system. Each is ordered
            best-first; ``rankings[i][0]`` is that system's top hit. Ids may
            repeat across lists (that is the point) but should be unique within
            a single list.
        k: The RRF constant. Larger ``k`` flattens the contribution of rank, so
            top ranks matter less. Must be positive.

    Returns:
        ``(id, score)`` pairs sorted by descending fused score. Ties are broken
        by ``str(id)`` so the output is fully deterministic.
    """

    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")

    scores: dict[Hashable, float] = {}
    for ranking in rankings:
        for rank, id_ in enumerate(ranking):
            # ``rank`` is 0-based; RRF uses 1-based ranks, hence ``+ 1``.
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)

    return sorted(scores.items(), key=lambda pair: (-pair[1], str(pair[0])))
