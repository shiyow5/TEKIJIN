"""Tests for the offline evaluation runner (#33 / technical-spec §7).

Layers:
* pure metric functions — exact, hand-computed expectations;
* dataset loader — the primary ``eval_person.json`` parses + strict validation;
* the runner — orchestration via a deterministic stub ranker, plus a real
  retrieval+scorer pipeline pass over the seed (a regression floor, not the spec
  target: with NULL fixture embeddings BM25 drives retrieval, so absolute numbers
  are lower than the fully-embedded target — we assert the pipeline is wired and
  clears a floor rather than betting CI on absolute accuracy).
"""

from __future__ import annotations

import datetime as dt

import pytest

from tekijin.eval.dataset import VALID_ROUTES, EvalQuery, load_eval_queries
from tekijin.eval.metrics import (
    QueryResult,
    evaluate,
    evaluate_alt,
    evaluate_by_difficulty,
    recall_at_k,
    reciprocal_rank,
    top1_hit,
)
from tekijin.eval.runner import RankResult, format_report, run_eval

# Anchored just after the fixtures' latest answer (2026-08-21) so the scorer's
# 7-day load window contains recent activity (matches the CLI's EVAL_NOW).
NOW = dt.datetime(2026, 8, 22, 0, 0, 0)


def _qr(
    ranked, gold, predicted_route="person", gold_route="person", difficulty="L1", gold_alt=None
) -> QueryResult:
    return QueryResult(
        ranked_experts=ranked,
        gold_experts=gold,
        predicted_route=predicted_route,
        gold_route=gold_route,
        difficulty=difficulty,
        gold_experts_alt=gold_alt or [],
    )


def _q(**kw) -> EvalQuery:
    base = {
        "id": 1,
        "query": "q",
        "gold_topics": ["t"],
        "gold_experts": [1],
        "gold_route": "person",
        "difficulty": "L1",
        "expect_abstain": False,
        "gold_experts_alt": [],
    }
    base.update(kw)
    return EvalQuery(**base)


# --------------------------------------------------------------------------- #
# metrics (pure)
# --------------------------------------------------------------------------- #
def test_top1_hit() -> None:
    assert top1_hit(_qr([3, 1, 2], [3, 9])) is True
    assert top1_hit(_qr([1, 3], [3, 9])) is False
    assert top1_hit(_qr([], [3])) is False  # empty ranking never hits


def test_recall_at_k_normalises_by_min_k_and_gold() -> None:
    assert recall_at_k(_qr([1, 2, 9, 4], [1, 2, 3]), 3) == pytest.approx(2 / 3)
    # gold larger than k: denominator capped at k so a full top-k can reach 1.0.
    assert recall_at_k(_qr([1, 2, 3], [1, 2, 3, 4]), 3) == pytest.approx(1.0)
    assert recall_at_k(_qr([1, 2], []), 3) == 0.0  # no gold -> 0.0


def test_recall_at_k_rejects_nonpositive_k() -> None:
    with pytest.raises(ValueError):
        recall_at_k(_qr([1], [1]), 0)


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(_qr([9, 3, 1], [3])) == pytest.approx(1 / 2)  # first hit at index 1
    assert reciprocal_rank(_qr([3, 9], [3])) == pytest.approx(1.0)
    assert reciprocal_rank(_qr([9, 8], [3])) == 0.0  # miss


def test_evaluate_excludes_goldless_and_nonabc_routes() -> None:
    results = [
        # top1 hit, recall@3 = 1/2, route match (person)
        _qr([1, 5, 6], [1, 2], predicted_route="person", gold_route="person"),
        # first hit at rank 2, recall@3 = 1/2, route miss (predicted document)
        _qr([9, 2, 8], [2, 7], predicted_route="document", gold_route="person"),
        # abstain query: no gold experts (excluded from ranking) AND gold_route
        # "none" (excluded from route accuracy) — contributes only to n.
        _qr([], [], predicted_route="person", gold_route="none"),
    ]
    m = evaluate(results)
    assert m.n == 3
    assert m.n_ranked == 2  # goldless query excluded from ranking metrics
    assert m.n_routed == 2  # "none"-route query excluded from route accuracy
    assert m.top1_accuracy == pytest.approx(0.5)
    assert m.recall_at_3 == pytest.approx(0.5)
    assert m.mrr == pytest.approx((1.0 + 0.5) / 2)
    assert m.route_accuracy == pytest.approx(0.5)  # 1 of 2 A/B/C routes match

    d = m.as_dict()
    assert set(d) == {
        "n",
        "n_ranked",
        "n_routed",
        "top1_accuracy",
        "recall_at_3",
        "mrr",
        "route_accuracy",
    }
    assert d["n"] == 3 and d["n_ranked"] == 2 and d["n_routed"] == 2


def test_evaluate_by_difficulty_splits_per_layer() -> None:
    results = [
        _qr([1], [1], difficulty="L1"),  # L1 top1 hit
        _qr([9], [1], difficulty="L2"),  # L2 miss
        _qr([2], [2], difficulty="L2"),  # L2 hit
    ]
    by_layer = evaluate_by_difficulty(results)
    assert set(by_layer) == {"L1", "L2"}
    assert by_layer["L1"].top1_accuracy == pytest.approx(1.0)
    assert by_layer["L2"].top1_accuracy == pytest.approx(0.5)  # 1 of 2
    assert by_layer["L2"].n_ranked == 2


def test_evaluate_alt_uses_alternate_gold_only_where_present() -> None:
    results = [
        _qr([5], [1], gold_alt=[5]),  # alt hit (primary would miss)
        _qr([9], [2], gold_alt=[2]),  # alt miss
        _qr([1], [1], gold_alt=[]),  # no alt -> excluded from the alt run
    ]
    alt = evaluate_alt(results)
    assert alt.n_ranked == 2  # only the two rows with alt labels
    assert alt.top1_accuracy == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# dataset loader (primary eval_person.json + strict validation)
# --------------------------------------------------------------------------- #
def test_load_bundled_eval_person() -> None:
    queries = load_eval_queries()
    assert len(queries) == 71  # eval_person.json v2 (fixtures README)
    assert all(isinstance(q, EvalQuery) for q in queries)
    assert {q.gold_route for q in queries} <= VALID_ROUTES
    # L4 abstain queries carry no gold experts and route "none".
    l4 = [q for q in queries if q.difficulty == "L4"]
    assert l4 and all(not q.gold_experts and q.gold_route == "none" for q in l4)
    # The independent alternate labels are present on a sizeable slice (README: 45).
    assert sum(1 for q in queries if q.gold_experts_alt) >= 40


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


def _row(**over) -> str:
    import json

    base = {
        "id": 1,
        "query": "q",
        "gold_topics": ["t"],
        "gold_experts": [1],
        "gold_route": "person",
        "difficulty": "L1",
    }
    base.update(over)
    return json.dumps([base])


def test_load_eval_queries_rejects_bad_route(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(_row(gold_route="preson"), encoding="utf-8")  # typo
    with pytest.raises(ValueError, match="gold_route"):
        load_eval_queries(bad)


def test_load_eval_queries_rejects_non_string_query(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(_row(query=None), encoding="utf-8")  # null must not become "None"
    with pytest.raises(ValueError, match="expected a string"):
        load_eval_queries(bad)


def test_load_eval_queries_rejects_boolean_expert_id(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(_row(gold_experts=[True]), encoding="utf-8")  # bool must not become 1
    with pytest.raises(ValueError, match="expected an integer"):
        load_eval_queries(bad)


def test_load_eval_queries_rejects_non_list_field(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(_row(gold_topics="t"), encoding="utf-8")
    with pytest.raises(ValueError, match="expected a list"):
        load_eval_queries(bad)


def test_load_eval_queries_rejects_non_bool_expect_abstain(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(_row(expect_abstain="yes"), encoding="utf-8")
    with pytest.raises(ValueError, match="expected a boolean"):
        load_eval_queries(bad)


# --------------------------------------------------------------------------- #
# pipeline: prior_answer responder pinning (mirrors the production graph)
# --------------------------------------------------------------------------- #
def test_pinned_responder_picks_highest_scoring_past_answer() -> None:
    from tekijin.eval.pipeline import _pinned_responder

    retrieval = {
        "past_answers": [
            {"qa_id": "a", "score": 0.4, "responder_id": 7},
            {"qa_id": "b", "score": 0.9, "responder_id": 3},  # highest score
            {"qa_id": "c", "score": 0.8, "responder_id": None},
        ],
    }
    assert _pinned_responder(retrieval) == 3  # type: ignore[arg-type]
    assert _pinned_responder({"past_answers": []}) is None  # type: ignore[arg-type]
    # highest score has no responder -> fall through to the next with one.
    only_none = {"past_answers": [{"qa_id": "a", "score": 0.9, "responder_id": None}]}
    assert _pinned_responder(only_none) is None  # type: ignore[arg-type]


def test_pipeline_ranker_pins_responder_on_prior_answer_route() -> None:
    from tekijin.eval.pipeline import PipelineRanker

    class _FakeRetriever:
        def search(self, query: str):  # noqa: ARG002
            # High answer confidence + a past answer -> decide_route == prior_answer.
            return {
                "past_answers": [{"qa_id": "a", "score": 0.95, "responder_id": 3}],
                "documents": [],
                "candidate_people": [1, 2, 3, 4],
                "answer_confidence": 0.99,
                "document_confidence": 0.0,
                "people_confidence": 0.2,
            }

    seen: dict[str, object] = {}

    class _FakeScorer:
        def rank(self, topics, candidate_ids, asker_id, now, *, top_k=3):  # noqa: ARG002
            seen["candidate_ids"] = list(candidate_ids)
            return {"recommendations": [{"person_id": pid} for pid in candidate_ids]}

    ranker = PipelineRanker(retriever=_FakeRetriever(), scorer=_FakeScorer(), now=NOW)
    result = ranker(_q(gold_route="prior_answer"))

    assert result.route == "prior_answer"
    # Only the pinned past responder is scored, not the whole candidate pool.
    assert seen["candidate_ids"] == [3]
    assert result.ranked_experts == [3]


# --------------------------------------------------------------------------- #
# runner (stub ranker — deterministic orchestration)
# --------------------------------------------------------------------------- #
def test_run_eval_with_perfect_stub() -> None:
    queries = [
        _q(id=1, gold_experts=[3, 1], gold_route="person"),
        _q(id=2, gold_experts=[5], gold_route="document"),
    ]

    def perfect(query: EvalQuery) -> RankResult:
        return RankResult(ranked_experts=list(query.gold_experts), route=query.gold_route)

    report = run_eval(queries, perfect)
    assert report.metrics.top1_accuracy == pytest.approx(1.0)
    assert report.metrics.recall_at_3 == pytest.approx(1.0)
    assert report.metrics.mrr == pytest.approx(1.0)
    assert report.metrics.route_accuracy == pytest.approx(1.0)
    assert len(report.results) == 2


def test_run_eval_with_empty_stub_scores_zero() -> None:
    queries = [_q(id=1, gold_experts=[3], gold_route="person")]
    report = run_eval(queries, lambda _q: RankResult(ranked_experts=[], route="document"))
    assert report.metrics.top1_accuracy == 0.0
    assert report.metrics.recall_at_3 == 0.0
    assert report.metrics.mrr == 0.0
    assert report.metrics.route_accuracy == 0.0


def test_format_report_contains_all_metrics_and_breakdowns() -> None:
    queries = [
        _q(id=1, difficulty="L1", gold_experts=[1], gold_experts_alt=[1]),
        _q(id=2, difficulty="L2", gold_experts=[2], gold_experts_alt=[9]),
    ]
    report = run_eval(queries, lambda q: RankResult(list(q.gold_experts), "person"))
    text = format_report(report)
    for label in ("Top-1 Accuracy", "Recall@3", "MRR", "Route Accuracy"):
        assert label in text
    assert "層別" in text and "L1" in text and "L2" in text  # per-layer breakdown
    assert "第2正解" in text  # anti-circularity (alt) line
    # report structure
    assert set(report.by_difficulty) == {"L1", "L2"}
    assert report.metrics_alt.n_ranked == 2


# --------------------------------------------------------------------------- #
# integration: the real pipeline over the seed (regression floor)
# --------------------------------------------------------------------------- #
def test_run_eval_real_pipeline_over_seed(seed_counts, session, fake_embedder) -> None:
    from tekijin.eval.pipeline import build_pipeline_ranker

    queries = load_eval_queries()
    ranker = build_pipeline_ranker(session, fake_embedder, now=NOW)
    report = run_eval(queries, ranker)

    m = report.metrics
    assert m.n == 71  # ran over every query
    assert m.n_ranked > 0 and m.n_routed > 0
    for value in (m.top1_accuracy, m.recall_at_3, m.mrr, m.route_accuracy):
        assert 0.0 <= value <= 1.0
    # Layer-wise breakdown + anti-circularity run are produced.
    assert {"L1", "L2", "L3"} <= set(report.by_difficulty)
    assert report.metrics_alt.n_ranked > 0
    # Sanity: the alt (answers-derived) Recall@3 is not a perfect echo of the
    # primary — if it were 1.0 the scorer would just be reproducing labels.
    assert report.metrics_alt.recall_at_3 < 0.99
    # Regression floors with headroom below the deterministic observed values on
    # the NON-leaky set (Top-1 0.66 / Recall@3 0.61 / MRR 0.71 / route 0.70).
    # These catch a real retrieval/scoring regression without pinning the exact
    # numbers (which move when the scorer weights are retuned). Absolute spec
    # targets (§7) need real embeddings on the seed rows; here the fixtures store
    # NULL vectors so BM25 drives retrieval and routing degenerates to the person
    # line (route accuracy == the person fraction among routed queries).
    assert m.top1_accuracy >= 0.55
    assert m.recall_at_3 >= 0.50
    assert m.mrr >= 0.60
    assert m.route_accuracy >= 0.55
