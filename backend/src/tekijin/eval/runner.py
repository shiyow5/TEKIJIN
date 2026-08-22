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


def run_eval(queries: Sequence[EvalQuery], ranker: Ranker) -> EvalReport:
    """Run ``ranker`` over every query and aggregate into an :class:`EvalReport`."""

    results: list[QueryResult] = []
    for query in queries:
        ranked = ranker(query)
        results.append(
            QueryResult(
                ranked_experts=list(ranked.ranked_experts),
                gold_experts=list(query.gold_experts),
                predicted_route=ranked.route,
                gold_route=query.gold_route,
            )
        )
    return EvalReport(metrics=evaluate(results), results=results)


def format_report(report: EvalReport) -> str:
    """Render the metrics as a human-readable block (spec §7 targets in parens).

    The ranking metrics use the eval set's gold topics (isolating retrieval +
    scoring from the C1 intent step), so treat them as a layer-1/2 measurement,
    not a full end-to-end accuracy.
    """

    m = report.metrics
    return (
        "評価結果 (technical-spec §7 / eval_person.json)\n"
        f"  queries        : {m.n} (ranked {m.n_ranked}, routed {m.n_routed})\n"
        f"  Top-1 Accuracy : {m.top1_accuracy:.3f} (目標 0.70)\n"
        f"  Recall@3       : {m.recall_at_3:.3f} (目標 0.90)\n"
        f"  MRR            : {m.mrr:.3f} (目標 0.75)\n"
        f"  Route Accuracy : {m.route_accuracy:.3f} (目標 0.80)\n"
        "  ※ gold topics を使用（層1-2の測定）。route/dense 指標は埋め込み索引が前提。"
    )
