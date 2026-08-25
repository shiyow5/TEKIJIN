"""Reciprocal Rank Fusion (RRF).

RRF combines several ranked lists into one by summing ``1 / (k + rank)`` over
every list a document appears in. It fuses systems whose scores live on
different scales — dense cosine similarity and BM25 term-frequency scores — using
*ranks only*, so no per-system score normalisation or tuning is required
(technical-spec §3.4, model-definition C4). The default ``k = 60`` follows the
original Cormack et al. (2009) formulation and the spec.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence


def adaptive_bm25_weight(
    dense_confidence: float,
    *,
    base: float,
    boosted: float | None,
    lo: float,
    hi: float,
) -> float:
    """BM25 RRF weight scaled by the dense channel's signal strength (#114).

    A fixed low BM25 weight (``base``, the #68 symptom-query optimum) starves the
    exact-match cases — a bare product name / model number / error code where the
    dense embedding is semantically uninformed (its top cosine is *low*) yet BM25
    points straight at the answer. So when the dense channel is weak we raise BM25
    toward ``boosted``; when dense is confident we keep it at ``base``:

    * ``dense_confidence <= lo``  -> ``boosted`` (dense uninformed; let BM25 lead)
    * ``dense_confidence >= hi``  -> ``base``    (dense confident; keep BM25 low)
    * between                     -> linear interpolation

    ``boosted is None`` (or ``boosted <= base``, or a non-increasing ``lo..hi``
    window) returns ``base`` flat — the pre-#114 fixed-weight behaviour, so the
    feature is inert until ``boosted`` is set and the window is tuned. ``base``
    must be non-negative; ``dense_confidence`` is a cosine in ``[0, 1]``.
    """

    if boosted is None or boosted <= base or hi <= lo:
        return base
    if dense_confidence <= lo:
        return boosted
    if dense_confidence >= hi:
        return base
    frac = (dense_confidence - lo) / (hi - lo)  # 0 at lo, 1 at hi
    return boosted + (base - boosted) * frac


def rrf(
    rankings: Sequence[Sequence[Hashable]],
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[tuple[Hashable, float]]:
    """Fuse ranked id lists into a single ranking via Reciprocal Rank Fusion.

    ``score(d) = Σ_r w_r / (k + rank_r(d))`` over every list ``r`` that contains
    ``d`` (1-based rank).

    Args:
        rankings: One ranked list per retrieval system. Each is ordered
            best-first; ``rankings[i][0]`` is that system's top hit. Ids may
            repeat across lists (that is the point) but should be unique within
            a single list.
        k: The RRF constant. Larger ``k`` flattens the contribution of rank, so
            top ranks matter less. Must be positive.
        weights: Optional per-ranking weight ``w_r`` (same length/order as
            ``rankings``). ``None`` weights every list at 1.0 (classic RRF). Used
            to down-weight a channel whose ranking is unreliable for the query
            style — e.g. BM25 on symptom-worded queries (#68). Weights must be
            non-negative; a length mismatch is rejected.

    Returns:
        ``(id, score)`` pairs sorted by descending fused score. Ties are broken
        by ``str(id)`` so the output is fully deterministic.
    """

    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if weights is not None:
        if len(weights) != len(rankings):
            raise ValueError(
                f"weights length {len(weights)} must match rankings length {len(rankings)}"
            )
        # Reject NaN/inf: NaN passes ``< 0`` yet poisons every score, and inf
        # makes one channel dominate — neither defines a usable ranking.
        if any(not math.isfinite(w) or w < 0 for w in weights):
            raise ValueError(f"weights must be finite and non-negative, got {list(weights)}")

    scores: dict[Hashable, float] = {}
    for idx, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else weights[idx]
        # A zero-weight ranking must contribute NOTHING — skip it entirely so its
        # ids are not even introduced (else a disabled channel could still surface
        # ids when the weighted channels return fewer than top_k).
        if weight == 0.0:
            continue
        for rank, id_ in enumerate(ranking):
            # ``rank`` is 0-based; RRF uses 1-based ranks, hence ``+ 1``.
            scores[id_] = scores.get(id_, 0.0) + weight / (k + rank + 1)

    return sorted(scores.items(), key=lambda pair: (-pair[1], str(pair[0])))
