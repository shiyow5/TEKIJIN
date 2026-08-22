"""Drive a ranker over the eval query set and aggregate the metrics.

The ranker is injected (a plain callable ``EvalQuery -> RankResult``) so the
orchestration is testable with a deterministic stub and the *same* code path runs
the real retrieval+scorer pipeline (see :mod:`tekijin.eval.pipeline`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tekijin.eval.dataset import EvalQuery
from tekijin.eval.metrics import (
    EvalMetrics,
    QueryResult,
    evaluate,
    evaluate_alt,
    evaluate_by_difficulty,
)


@dataclass(frozen=True)
class RankResult:
    """A ranker's output for one query: the expert ranking + the chosen route."""

    ranked_experts: list[int]
    route: str


# A ranker maps one eval query to its predicted ranking + route.
Ranker = Callable[[EvalQuery], RankResult]


@dataclass(frozen=True)
class EvalReport:
    """Aggregate metrics + per-layer breakdown + the anti-circularity (alt) run.

    ``metrics`` is the overall primary-label result; ``by_difficulty`` splits it
    per L1–L4 (a healthy aggregate can hide an L2/L3 regression); ``metrics_alt``
    re-scores ranking against the independent ``answers``-derived gold. ``results``
    keeps every per-query record for drill-down.
    """

    metrics: EvalMetrics
    by_difficulty: dict[str, EvalMetrics]
    metrics_alt: EvalMetrics
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
                difficulty=query.difficulty,
                gold_experts_alt=list(query.gold_experts_alt),
            )
        )
    return EvalReport(
        metrics=evaluate(results),
        by_difficulty=evaluate_by_difficulty(results),
        metrics_alt=evaluate_alt(results),
        results=results,
    )


def format_report(report: EvalReport) -> str:
    """Render the metrics as a human-readable block (spec §7 targets in parens).

    The ranking metrics use the eval set's gold topics (isolating retrieval +
    scoring from the C1 intent step), so treat them as a layer-1/2 measurement,
    not a full end-to-end accuracy.
    """

    m = report.metrics
    lines = [
        "評価結果 (technical-spec §7 / eval_person.json)",
        f"  queries        : {m.n} (ranked {m.n_ranked}, routed {m.n_routed})",
        f"  Top-1 Accuracy : {m.top1_accuracy:.3f} (目標 0.70)",
        f"  Recall@3       : {m.recall_at_3:.3f} (目標 0.90)",
        f"  MRR            : {m.mrr:.3f} (目標 0.75)",
        f"  Route Accuracy : {m.route_accuracy:.3f} (目標 0.80, A/B/C のみ n={m.n_routed})",
        f"  Abstain Rate   : {m.abstain_accuracy:.3f} (棄却できた割合 n={m.n_abstain}) "
        "— 現状パイプラインは明示的な棄却経路を持たないため gap の可視化用",
        "  層別 Recall@3 (必ず層別に見る):",
    ]
    for layer, lm in report.by_difficulty.items():
        lines.append(
            f"    {layer}: R@3={lm.recall_at_3:.3f} Top-1={lm.top1_accuracy:.3f} (n={lm.n_ranked})"
        )
    alt = report.metrics_alt
    lines.append(
        f"  第2正解(answers派生) Recall@3 : {alt.recall_at_3:.3f} (n={alt.n_ranked}) "
        "— 循環チェック（主 gold を再現しただけでないか）"
    )
    lines.append("  ※ gold topics を使用（層1-2の測定）。route/dense 指標は埋め込み索引が前提。")
    return "\n".join(lines)
