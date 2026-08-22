"""Drive a ranker over the eval query set and aggregate the metrics.

The ranker is injected (a plain callable ``EvalQuery -> RankResult``) so the
orchestration is testable with a deterministic stub and the *same* code path runs
the real retrieval+scorer pipeline (see :mod:`tekijin.eval.pipeline`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tekijin.eval.dataset import EvalQuery
from tekijin.eval.metrics import EvalMetrics, QueryResult, evaluate


@dataclass(frozen=True)
class RankResult:
    """A ranker's output for one query: the expert ranking + the chosen route."""

    ranked_experts: list[int]
    route: str


# A ranker maps one eval query to its predicted ranking + route.
Ranker = Callable[[EvalQuery], RankResult]


@dataclass(frozen=True)
class EvalReport:
    """The aggregate metrics plus every per-query result (for drill-down)."""

    metrics: EvalMetrics
    results: list[QueryResult]


def run_eval(queries: Sequence[EvalQuery], ranker: Ranker, *, k: int = 3) -> EvalReport:
    """Run ``ranker`` over every query and aggregate into an :class:`EvalReport`."""

    results = [
        QueryResult(
            ranked_experts=list(result.ranked_experts),
            gold_experts=list(query.correct_experts),
            predicted_route=result.route,
            gold_route=query.route,
        )
        for query in queries
        for result in (ranker(query),)
    ]
    return EvalReport(metrics=evaluate(results, k=k), results=results)


def format_report(report: EvalReport) -> str:
    """Render the metrics as a human-readable block (spec §7 targets in parens)."""

    m = report.metrics
    return (
        "評価結果 (technical-spec §7)\n"
        f"  queries        : {m.n} (ranked {m.n_ranked})\n"
        f"  Top-1 Accuracy : {m.top1_accuracy:.3f} (目標 0.70)\n"
        f"  Recall@3       : {m.recall_at_3:.3f} (目標 0.90)\n"
        f"  MRR            : {m.mrr:.3f} (目標 0.75)\n"
        f"  Route Accuracy : {m.route_accuracy:.3f} (目標 0.80)"
    )
