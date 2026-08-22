"""Tests for the offline evaluation runner (#33 / technical-spec §7).

Three layers:
* pure metric functions — exact, hand-computed expectations;
* dataset loader — the bundled 40-item set parses and validates;
* the runner — orchestration via a deterministic stub ranker, plus a real
  retrieval+scorer pipeline pass over the seed (a regression floor, not the spec
  target: with NULL fixture embeddings BM25 drives retrieval, so absolute numbers
  are lower than the fully-embedded target — we assert the pipeline is wired and
  beats an empty ranking rather than betting CI on absolute accuracy).
"""

from __future__ import annotations

import datetime as dt

import pytest

from tekijin.eval.dataset import EvalQuery, load_eval_queries
from tekijin.eval.metrics import (
    QueryResult,
    evaluate,
    recall_at_k,
    reciprocal_rank,
    top1_hit,
)
from tekijin.eval.runner import RankResult, format_report, run_eval

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)


def _qr(ranked, gold, predicted_route="person", gold_route="person") -> QueryResult:
    return QueryResult(
        ranked_experts=ranked,
        gold_experts=gold,
        predicted_route=predicted_route,
        gold_route=gold_route,
    )


# --------------------------------------------------------------------------- #
# metrics (pure)
# --------------------------------------------------------------------------- #
def test_top1_hit() -> None:
    assert top1_hit(_qr([3, 1, 2], [3, 9])) is True
    assert top1_hit(_qr([1, 3], [3, 9])) is False
    assert top1_hit(_qr([], [3])) is False  # empty ranking never hits


def test_recall_at_k_normalises_by_min_k_and_gold() -> None:
    # 2 of 3 gold in top-3, |gold|=3 -> 2/3.
    assert recall_at_k(_qr([1, 2, 9, 4], [1, 2, 3]), 3) == pytest.approx(2 / 3)
    # gold larger than k: denominator capped at k so a full top-k can reach 1.0.
    assert recall_at_k(_qr([1, 2, 3], [1, 2, 3, 4]), 3) == pytest.approx(1.0)
    # no gold -> 0.0 (route-only query).
    assert recall_at_k(_qr([1, 2], []), 3) == 0.0


def test_recall_at_k_rejects_nonpositive_k() -> None:
    with pytest.raises(ValueError):
        recall_at_k(_qr([1], [1]), 0)


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(_qr([9, 3, 1], [3])) == pytest.approx(1 / 2)  # first hit at index 1
    assert reciprocal_rank(_qr([3, 9], [3])) == pytest.approx(1.0)
    assert reciprocal_rank(_qr([9, 8], [3])) == 0.0  # miss


def test_evaluate_aggregates_and_excludes_goldless_from_ranking() -> None:
    results = [
        # top1 hit, recall@3 = 1/2, route match
        _qr([1, 5, 6], [1, 2], predicted_route="person", gold_route="person"),
        # first hit at rank 2, recall@3 = 1/2, route miss
        _qr([9, 2, 8], [2, 7], predicted_route="document", gold_route="person"),
        # route-only query (no gold experts) — excluded from ranking metrics
        _qr([], [], predicted_route="prior_answer", gold_route="prior_answer"),
    ]
    m = evaluate(results, k=3)
    assert m.n == 3
    assert m.n_ranked == 2  # the goldless query is excluded from ranking metrics
    assert m.top1_accuracy == pytest.approx(0.5)  # 1 of 2 ranked queries
    assert m.recall_at_3 == pytest.approx((0.5 + 0.5) / 2)  # 1/2 and 1/2
    assert m.mrr == pytest.approx((1.0 + 0.5) / 2)
    assert m.route_accuracy == pytest.approx(2 / 3)  # 2 of 3 routes match

    # as_dict exposes every metric under a stable key (for JSON export / logging).
    d = m.as_dict()
    assert set(d) == {"n", "n_ranked", "top1_accuracy", "recall_at_3", "mrr", "route_accuracy"}
    assert d["n"] == 3 and d["n_ranked"] == 2


# --------------------------------------------------------------------------- #
# dataset loader
# --------------------------------------------------------------------------- #
def test_load_bundled_eval_queries() -> None:
    queries = load_eval_queries()
    assert len(queries) == 40
    assert all(isinstance(q, EvalQuery) for q in queries)
    assert all(q.correct_experts for q in queries)  # every query has gold experts
    assert {q.route for q in queries} <= {"person", "prior_answer", "document"}


def test_load_eval_queries_rejects_missing_keys(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('[{"id": 1, "query": "q"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="missing keys"):
        load_eval_queries(bad)


def test_load_eval_queries_rejects_non_list(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"id": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON list"):
        load_eval_queries(bad)


def test_load_eval_queries_rejects_non_object_row(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[123]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        load_eval_queries(bad)


def test_load_eval_queries_rejects_non_list_field(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        '[{"id": 1, "query": "q", "topics": "t", "correct_experts": [1], "route": "person"}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected a list"):
        load_eval_queries(bad)


# --------------------------------------------------------------------------- #
# runner (stub ranker — deterministic orchestration)
# --------------------------------------------------------------------------- #
def test_run_eval_with_perfect_stub() -> None:
    queries = [
        EvalQuery(id=1, query="a", topics=["t"], correct_experts=[3, 1], route="person"),
        EvalQuery(id=2, query="b", topics=["t"], correct_experts=[5], route="document"),
    ]

    def perfect(query: EvalQuery) -> RankResult:
        return RankResult(ranked_experts=list(query.correct_experts), route=query.route)

    report = run_eval(queries, perfect)
    assert report.metrics.top1_accuracy == pytest.approx(1.0)
    assert report.metrics.recall_at_3 == pytest.approx(1.0)
    assert report.metrics.mrr == pytest.approx(1.0)
    assert report.metrics.route_accuracy == pytest.approx(1.0)
    assert len(report.results) == 2


def test_run_eval_with_empty_stub_scores_zero() -> None:
    queries = [EvalQuery(id=1, query="a", topics=["t"], correct_experts=[3], route="person")]

    def empty(_query: EvalQuery) -> RankResult:
        return RankResult(ranked_experts=[], route="document")

    report = run_eval(queries, empty)
    assert report.metrics.top1_accuracy == 0.0
    assert report.metrics.recall_at_3 == 0.0
    assert report.metrics.mrr == 0.0
    assert report.metrics.route_accuracy == 0.0


def test_format_report_contains_all_metrics() -> None:
    queries = [EvalQuery(id=1, query="a", topics=["t"], correct_experts=[3], route="person")]
    report = run_eval(queries, lambda q: RankResult([3], "person"))
    text = format_report(report)
    for label in ("Top-1 Accuracy", "Recall@3", "MRR", "Route Accuracy"):
        assert label in text


# --------------------------------------------------------------------------- #
# integration: the real pipeline over the seed (regression floor)
# --------------------------------------------------------------------------- #
def test_run_eval_real_pipeline_over_seed(seed_counts, session, fake_embedder) -> None:
    from tekijin.eval.pipeline import build_pipeline_ranker

    queries = load_eval_queries()
    ranker = build_pipeline_ranker(session, fake_embedder, now=NOW)
    report = run_eval(queries, ranker)

    m = report.metrics
    assert m.n == 40 and m.n_ranked == 40  # ran over every query
    # Valid metric ranges.
    for value in (m.top1_accuracy, m.recall_at_3, m.mrr, m.route_accuracy):
        assert 0.0 <= value <= 1.0
    # Regression floors, set with headroom below the deterministic observed values
    # (Top-1 0.70 / Recall@3 0.81 / MRR 0.83 on this seed). These catch a real
    # wiring/scoring regression without pinning the exact numbers (which move when
    # the scorer weights are retuned on the eval set). Absolute spec targets
    # (§7: 0.70 / 0.90 / 0.75) need real embeddings on the seed rows; here the
    # fixtures store NULL vectors so BM25 drives retrieval and routing degenerates
    # to the person line (route accuracy == the person fraction).
    assert m.top1_accuracy >= 0.55
    assert m.recall_at_3 >= 0.65
    assert m.mrr >= 0.65
    assert m.route_accuracy >= 0.50
