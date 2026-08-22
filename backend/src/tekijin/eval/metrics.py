"""Ranking / routing metrics for offline evaluation (technical-spec §7).

Pure functions over :class:`QueryResult` records — no DB, no model — so the
metric definitions are fixed and unit-tested independently of how the ranking was
produced. Metric set matches the spec table: Top-1 Accuracy, Recall@3, MRR, and
route (分岐) accuracy.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# The three A/B/C route branches the router can produce. Route accuracy is scored
# only over queries whose gold route is one of these — an abstain (``none``) gold
# is measured by the robustness set, not the A/B/C branch metric (spec §7).
ROUTE_LABELS = frozenset({"person", "prior_answer", "document"})

# Recall is reported at this fixed cutoff (spec §7 "Recall@3").
RECALL_K = 3


@dataclass(frozen=True)
class QueryResult:
    """One query's predicted ranking + route, paired with its gold labels.

    ``ranked_experts`` is the predicted expert-id ranking (best first);
    ``gold_experts`` is the (unordered) set of correct expert ids.
    """

    ranked_experts: list[int]
    gold_experts: list[int]
    predicted_route: str
    gold_route: str


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


@dataclass(frozen=True)
class EvalMetrics:
    """Aggregate metrics over an evaluation run."""

    n: int  # total queries evaluated
    n_ranked: int  # queries with gold experts (contribute to ranking metrics)
    n_routed: int  # queries with an A/B/C gold route (contribute to route accuracy)
    top1_accuracy: float
    recall_at_3: float
    mrr: float
    route_accuracy: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "n": self.n,
            "n_ranked": self.n_ranked,
            "n_routed": self.n_routed,
            "top1_accuracy": self.top1_accuracy,
            "recall_at_3": self.recall_at_3,
            "mrr": self.mrr,
            "route_accuracy": self.route_accuracy,
        }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


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
    return EvalMetrics(
        n=len(results),
        n_ranked=len(ranked),
        n_routed=len(routed),
        top1_accuracy=_mean([1.0 if top1_hit(r) else 0.0 for r in ranked]),
        recall_at_3=_mean([recall_at_k(r, RECALL_K) for r in ranked]),
        mrr=_mean([reciprocal_rank(r) for r in ranked]),
        route_accuracy=_mean([1.0 if route_hit(r) else 0.0 for r in routed]),
    )
