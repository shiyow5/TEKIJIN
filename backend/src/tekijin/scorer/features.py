"""Pure scoring-feature functions (no DB, no clock).

Each maps already-gathered primitives (counts, dates, branches) to a scalar,
mostly in ``[0, 1]``, so the weighted sum in :mod:`tekijin.scorer.scorer` stays
interpretable. Time enters only through an injected ``now`` — nothing here reads
the wall clock — so every result is reproducible and unit-testable.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping

from tekijin.scorer.weights import (
    ANSWER_QUALITY_ANSWER_COEF,
    ANSWER_QUALITY_HELPFUL_COEF,
    ANSWER_QUALITY_REUSE_COEF,
    ANSWER_QUALITY_SCALE,
    LOAD_HALF_SATURATION,
    PROXIMITY_COMPANY_WIDE,
    PROXIMITY_SAME_BRANCH,
    PROXIMITY_SAME_REGION,
    RECENCY_HALF_LIFE_DAYS,
    REGION_OF_BRANCH,
)


def saturate(total: float, scale: float = 1.0) -> float:
    """Map an unbounded non-negative ``total`` into ``[0, 1)`` monotonically.

    ``1 - exp(-total/scale)``: 0 at 0, rising with diminishing returns, so
    stacking ever more evidence never exceeds 1 and the tenth item matters less
    than the first.
    """

    if total <= 0.0:
        return 0.0
    return 1.0 - math.exp(-total / scale)


def age_in_days(now: dt.datetime, when: dt.date | dt.datetime) -> float:
    """Whole days from ``when`` to ``now`` (never negative)."""

    moment = when if isinstance(when, dt.datetime) else dt.datetime(when.year, when.month, when.day)
    delta = (now - moment).total_seconds() / 86400.0
    return max(delta, 0.0)


def decay(age_days: float, half_life_days: float = RECENCY_HALF_LIFE_DAYS) -> float:
    """Exponential relevance decay: ``0.5 ** (age / half_life)`` (fresh -> 1.0)."""

    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def recency(now: dt.datetime, moments: Iterable[dt.date | dt.datetime]) -> float:
    """Decay of the most recent experience; ``0.0`` when there is none."""

    decays = [decay(age_in_days(now, m)) for m in moments]
    return max(decays) if decays else 0.0


def answer_quality(helpful_count: int, reuse_total: int, answer_count: int) -> float:
    """Monotonic quality of a person's on-topic answers, in ``[0, 1)``.

    A helpful answer counts most, reuse adds, and simply having answered gives a
    floor. Saturating so a prolific answerer cannot dominate on volume alone.
    """

    raw = (
        ANSWER_QUALITY_ANSWER_COEF * answer_count
        + ANSWER_QUALITY_HELPFUL_COEF * helpful_count
        + ANSWER_QUALITY_REUSE_COEF * reuse_total
    )
    return saturate(raw, ANSWER_QUALITY_SCALE)


def load(recent_count: int) -> float:
    """Workload penalty in ``[0, 1)`` from recent items (recs + answers).

    ``1 - 0.5 ** (count / LOAD_HALF_SATURATION)``: 0 when idle, 0.5 at the
    half-saturation count, approaching (never reaching) 1 under heavy load.
    """

    if recent_count <= 0:
        return 0.0
    return 1.0 - 0.5 ** (recent_count / LOAD_HALF_SATURATION)


def proximity(
    asker_branch: str | None,
    candidate_branch: str | None,
    region_map: Mapping[str, str] = REGION_OF_BRANCH,
) -> float:
    """Organisational closeness: same branch > same region > company-wide.

    Unknown branch on either side yields the neutral company-wide tier.
    """

    if not asker_branch or not candidate_branch:
        return PROXIMITY_COMPANY_WIDE
    if asker_branch == candidate_branch:
        return PROXIMITY_SAME_BRANCH
    asker_region = region_map.get(asker_branch)
    candidate_region = region_map.get(candidate_branch)
    if asker_region is not None and asker_region == candidate_region:
        return PROXIMITY_SAME_REGION
    return PROXIMITY_COMPANY_WIDE


def confidence_label(edge_weight: float, evidence_count: int) -> str:
    """Deterministic 高/中/低 from the topic edge weight and evidence count."""

    if edge_weight >= 0.7 and evidence_count >= 3:
        return "高"
    if edge_weight >= 0.4 or evidence_count >= 2:
        return "中"
    return "低"
