"""Ranking / routing metrics for offline evaluation (technical-spec §7).

Pure functions over :class:`QueryResult` records — no DB, no model — so the
metric definitions are fixed and unit-tested independently of how the ranking was
produced. Metric set matches the spec table: Top-1 Accuracy, Recall@3, MRR, and
route (分岐) accuracy.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

# The three A/B/C route branches the router can produce. Route accuracy is scored
# only over queries whose gold route is one of these — an abstain (``none``) gold
# is measured by the robustness set, not the A/B/C branch metric (spec §7).
ROUTE_LABELS = frozenset({"person", "prior_answer", "document"})

# Recall is reported at this fixed cutoff (spec §7 "Recall@3").
RECALL_K = 3

# Gold route for a query that should be declined (abstain / no expert exists).
ABSTAIN_ROUTE = "none"


@dataclass(frozen=True)
class QueryResult:
    """One query's predicted ranking + route, paired with its gold labels.

    ``ranked_experts`` is the predicted expert-id ranking (best first);
    ``gold_experts`` is the (unordered) set of correct expert ids. ``difficulty``
    (L1–L4) drives layer-wise reporting; ``gold_experts_alt`` is the independent
    second gold set (derived from ``answers``, not ``projects``) used for the
    anti-circularity check — empty when the query has no alternate labels.

    ``predicted_topics`` (best first) + ``gold_topics`` isolate stage A (query →
    topic) from the ranking stages: the ranking metrics feed the *gold* topics to
    the scorer, so a poor Recall@3 could be either weak retrieval OR weak topic
    inference. Scoring predicted vs gold topics separates the two (#71 / #65).
    """

    ranked_experts: list[int]
    gold_experts: list[int]
    predicted_route: str
    gold_route: str
    difficulty: str = ""
    gold_experts_alt: list[int] = field(default_factory=list)
    predicted_topics: list[str] = field(default_factory=list)
    gold_topics: list[str] = field(default_factory=list)


def _first_hit_rank(ranked: Sequence[int], gold: Iterable[int]) -> int | None:
    """0-based index of the first ranked id that is in ``gold`` (``None`` if none)."""

    gold_set = set(gold)
    for index, person_id in enumerate(ranked):
        if person_id in gold_set:
            return index
    return None


def top1_hit(result: QueryResult) -> bool:
    """True if the top-ranked expert is a correct one."""

    return bool(result.ranked_experts) and result.ranked_experts[0] in set(result.gold_experts)


def recall_at_k(result: QueryResult, k: int) -> float:
    """Fraction of the gold experts found in the top-``k`` (0 when no gold).

    The denominator is ``min(k, |gold|)`` so a query with more gold experts than
    ``k`` can still reach 1.0 — matching the eval-set baselines (analysis §5-4).
    """

    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if not result.gold_experts:
        return 0.0
    hit = len(set(result.ranked_experts[:k]) & set(result.gold_experts))
    return hit / min(k, len(result.gold_experts))


def reciprocal_rank(result: QueryResult) -> float:
    """1 / (rank of the first correct expert), or 0 when none are ranked."""

    rank = _first_hit_rank(result.ranked_experts, result.gold_experts)
    return 0.0 if rank is None else 1.0 / (rank + 1)


def route_hit(result: QueryResult) -> bool:
    """True if the predicted route matches the gold route."""

    return result.predicted_route == result.gold_route


def topic_hit_at_k(result: QueryResult, k: int) -> bool:
    """True if any of the top-``k`` predicted topics is a gold topic (stage A).

    ``acc@1`` == ``topic_hit_at_k(r, 1)`` (the single best-guess topic is correct),
    ``acc@3`` == ``topic_hit_at_k(r, 3)`` (a gold topic is anywhere in the top 3).
    Mirrors ``scripts/research_pipeline.py``'s ``topic_accuracy`` exactly.
    """

    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    return bool(set(result.predicted_topics[:k]) & set(result.gold_topics))


@dataclass(frozen=True)
class EvalMetrics:
    """Aggregate metrics over an evaluation run."""

    n: int  # total queries evaluated
    n_ranked: int  # queries with gold experts (contribute to ranking metrics)
    n_routed: int  # queries with an A/B/C gold route (contribute to route accuracy)
    n_abstain: int  # queries whose gold route is abstain ("none")
    top1_accuracy: float
    recall_at_3: float
    mrr: float
    route_accuracy: float
    # Fraction of abstain queries where the system produced NO experts (declined).
    # The current pipeline has no explicit abstain path, so this exposes the gap
    # rather than hiding it by dropping the abstain rows from every metric. NOTE:
    # on eval_person the L4 rows all have empty gold_topics, which the ranker maps
    # to an empty result by construction — so this reads ~1.0 and does NOT yet
    # demonstrate true no-expert detection (that is the robustness set's job).
    abstain_accuracy: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "n": self.n,
            "n_ranked": self.n_ranked,
            "n_routed": self.n_routed,
            "n_abstain": self.n_abstain,
            "top1_accuracy": self.top1_accuracy,
            "recall_at_3": self.recall_at_3,
            "mrr": self.mrr,
            "route_accuracy": self.route_accuracy,
            "abstain_accuracy": self.abstain_accuracy,
        }


@dataclass(frozen=True)
class TopicAccuracy:
    """Stage A (query → topic) hit-rate, scored against ``gold_topics`` (#71).

    Separated from the ranking metrics because those feed the *gold* topics to the
    scorer — so this is the ceiling-independent measure of how well topics are
    inferred. ``n_topic`` is the denominator: queries carrying gold topics (an
    abstain/unsupported row has none and is excluded, not counted as a miss).
    """

    n_topic: int
    acc_at_1: float
    acc_at_3: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "n_topic": self.n_topic,
            "acc_at_1": self.acc_at_1,
            "acc_at_3": self.acc_at_3,
        }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_topics(results: Sequence[QueryResult]) -> TopicAccuracy:
    """Aggregate stage-A topic hit-rate over queries that carry gold topics.

    Averaged only over rows with non-empty ``gold_topics`` — an abstain /
    unsupported-topic query has no gold topic to hit, so including it would
    conflate "no topic exists" with "topic missed".
    """

    scored = [r for r in results if r.gold_topics]
    return TopicAccuracy(
        n_topic=len(scored),
        acc_at_1=_mean([1.0 if topic_hit_at_k(r, 1) else 0.0 for r in scored]),
        acc_at_3=_mean([1.0 if topic_hit_at_k(r, 3) else 0.0 for r in scored]),
    )


def evaluate(results: Sequence[QueryResult]) -> EvalMetrics:
    """Aggregate per-query results into :class:`EvalMetrics`.

    Ranking metrics (Top-1 / Recall@3 / MRR) are averaged only over queries that
    carry gold experts — an abstain query has nobody to rank, so it would
    otherwise drag the average toward zero. Route accuracy is over queries whose
    gold route is an A/B/C branch (``none``/abstain is out of the branch metric).
    Recall is fixed at ``RECALL_K`` so the reported ``recall_at_3`` is never a
    mislabelled Recall@k for some other ``k``.
    """

    ranked = [r for r in results if r.gold_experts]
    routed = [r for r in results if r.gold_route in ROUTE_LABELS]
    abstain = [r for r in results if r.gold_route == ABSTAIN_ROUTE]
    return EvalMetrics(
        n=len(results),
        n_ranked=len(ranked),
        n_routed=len(routed),
        n_abstain=len(abstain),
        top1_accuracy=_mean([1.0 if top1_hit(r) else 0.0 for r in ranked]),
        recall_at_3=_mean([recall_at_k(r, RECALL_K) for r in ranked]),
        mrr=_mean([reciprocal_rank(r) for r in ranked]),
        route_accuracy=_mean([1.0 if route_hit(r) else 0.0 for r in routed]),
        # Declined correctly == produced no experts for an abstain query.
        abstain_accuracy=_mean([1.0 if not r.ranked_experts else 0.0 for r in abstain]),
    )


def evaluate_by_difficulty(results: Sequence[QueryResult]) -> dict[str, EvalMetrics]:
    """Per-layer metrics keyed by difficulty (L1/L2/L3/L4…), sorted by label.

    The primary eval set requires layer-wise reporting — a healthy aggregate can
    hide an L2/L3 regression (fixtures README: 「必ず層別に出す」).
    """

    layers = sorted({r.difficulty for r in results if r.difficulty})
    return {layer: evaluate([r for r in results if r.difficulty == layer]) for layer in layers}


def evaluate_alt(results: Sequence[QueryResult]) -> EvalMetrics:
    """Ranking metrics against the alternate gold labels (anti-circularity check).

    Scores only queries that carry ``gold_experts_alt`` (derived from ``answers``,
    an evidence source the primary gold deliberately avoids). A scorer that merely
    reproduces the primary label rule (project membership) will look strong on the
    primary metrics but not here (fixtures README §gold_experts_alt).
    """

    alt = [replace(r, gold_experts=r.gold_experts_alt) for r in results if r.gold_experts_alt]
    return evaluate(alt)
