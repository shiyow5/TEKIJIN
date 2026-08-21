"""Database-free unit tests for the agent's pure logic.

Covers the C5 route decision (all branches + tunable thresholds + determinism),
the conditional-edge routers, and the deterministic LLM stubs. No DB, no model,
no network.
"""

from __future__ import annotations

import pytest

from tekijin.agent import graph as graph_mod
from tekijin.agent.nodes import AgentNodes, _top_by_score
from tekijin.agent.route import (
    DOCUMENT,
    DOCUMENT_THRESHOLD,
    PERSON,
    PERSON_STRONG_THRESHOLD,
    PRIOR_ANSWER,
    PRIOR_ANSWER_THRESHOLD,
    decide_route,
)
from tekijin.agent.stubs import (
    MAX_FOLLOWUPS,
    KeywordIntentModel,
    RuleSufficiencyModel,
    TemplateDraftModel,
)


def _retrieval(*, answers=(), documents=(), people=()) -> dict:
    return {
        "past_answers": list(answers),
        "documents": list(documents),
        "candidate_people": list(people),
    }


# --------------------------------------------------------------------------- #
# C5 route decision
# --------------------------------------------------------------------------- #
def test_route_person_when_answer_signal_is_decent() -> None:
    # A decent (>= PERSON_STRONG_THRESHOLD) but not prior_answer-level answer keeps
    # the person route even with a document present; confidence is the top answer
    # score on the RRF scale (no cross-scale constant).
    top = PERSON_STRONG_THRESHOLD + 0.005
    r = _retrieval(
        answers=[{"qa_id": "a", "score": top, "responder_id": 7}],
        documents=[{"doc_id": "d", "score": DOCUMENT_THRESHOLD + 0.05}],
        people=[1, 2, 3],
    )
    decision = decide_route(r)
    assert decision.route == PERSON
    assert decision.confidence == pytest.approx(top)
    assert "主線" in decision.reason


def test_route_prior_answer_when_top_answer_clears_threshold() -> None:
    r = _retrieval(
        answers=[{"qa_id": "a", "score": PRIOR_ANSWER_THRESHOLD + 0.01, "responder_id": 7}],
        people=[1, 2],
    )
    decision = decide_route(r)
    assert decision.route == PRIOR_ANSWER
    assert decision.confidence == PRIOR_ANSWER_THRESHOLD + 0.01


def test_route_document_when_no_people_and_doc_clears_threshold() -> None:
    r = _retrieval(
        answers=[{"qa_id": "a", "score": 0.001, "responder_id": 7}],
        documents=[{"doc_id": "d", "score": DOCUMENT_THRESHOLD + 0.01}],
        people=[],  # weak person signal -> demotion allowed
    )
    decision = decide_route(r)
    assert decision.route == DOCUMENT
    assert "文書" in decision.reason


def test_route_person_fallback_when_nothing() -> None:
    decision = decide_route(_retrieval())
    assert decision.route == PERSON
    assert decision.confidence == 0.0


def test_route_document_reachable_with_weak_candidates() -> None:
    # Fix A: candidate people exist but the person signal is WEAK (no strong prior
    # answer), and a document clears its bar and out-scores the answer -> document
    # is genuinely reachable (it was dead before, hidden behind people=[]).
    r = _retrieval(
        answers=[{"qa_id": "a", "score": 0.005, "responder_id": 7}],  # weak
        documents=[{"doc_id": "d", "score": DOCUMENT_THRESHOLD + 0.05}],
        people=[1, 2, 3],  # present, but weak
    )
    decision = decide_route(r)
    assert decision.route == DOCUMENT
    assert decision.confidence == pytest.approx(DOCUMENT_THRESHOLD + 0.05)


def test_route_strong_answer_beats_document() -> None:
    # When the answer signal is strong enough, a document does not demote it.
    r = _retrieval(
        answers=[{"qa_id": "a", "score": PERSON_STRONG_THRESHOLD, "responder_id": 7}],
        documents=[{"doc_id": "d", "score": DOCUMENT_THRESHOLD + 0.1}],
        people=[1, 2, 3],
    )
    assert decide_route(r).route == PERSON


def test_route_thresholds_are_tunable() -> None:
    r = _retrieval(answers=[{"qa_id": "a", "score": 0.03, "responder_id": 1}], people=[1])
    # Raise the bar above the score -> falls back to person.
    assert decide_route(r, prior_answer_threshold=0.5).route == PERSON


def test_route_is_order_independent() -> None:
    a = [
        {"qa_id": "x", "score": 0.01, "responder_id": 1},
        {"qa_id": "y", "score": 0.03, "responder_id": 2},
    ]
    forward = decide_route(_retrieval(answers=a, people=[1]))
    backward = decide_route(_retrieval(answers=list(reversed(a)), people=[1]))
    assert forward == backward  # top score picked regardless of list order


# --------------------------------------------------------------------------- #
# conditional-edge routers
# --------------------------------------------------------------------------- #
def test_after_c1_routes_off_topic() -> None:
    assert graph_mod._after_c1({"out_of_scope": True}) == "off_topic"
    assert graph_mod._after_c1({"out_of_scope": False}) == "c2_sufficiency"


def test_after_c2_routes_on_sufficiency() -> None:
    assert graph_mod._after_c2({"sufficient": True}) == "c3_embed"
    assert graph_mod._after_c2({"sufficient": False}) == "ask"


def test_after_c5_returns_route() -> None:
    assert graph_mod._after_c5({"route": DOCUMENT}) == DOCUMENT
    assert graph_mod._after_c5({}) == PERSON  # default


def test_after_c6_and_send_routers() -> None:
    assert graph_mod._after_c6({"recommendations": [{"person_id": 1}]}) == "c7_draft"
    assert graph_mod._after_c6({"recommendations": []}) == "no_candidate"
    assert graph_mod._after_send({"outcome": "declined"}) == "reroute"
    assert graph_mod._after_send({"outcome": "accepted"}) == "c8_update"


def test_after_send_reconfirms_unexpected_outcome() -> None:
    # Fix B: anything but accepted/declined loops back to send (never c8_update).
    assert graph_mod._after_send({"outcome": None}) == "send"
    assert graph_mod._after_send({"outcome": "garbage"}) == "send"
    assert graph_mod._after_send({}) == "send"


# --------------------------------------------------------------------------- #
# node helpers + reset validation (Fix I, K)
# --------------------------------------------------------------------------- #
def _nodes() -> AgentNodes:
    class _Stub:
        def analyze(self, *a, **k):  # pragma: no cover - not called here
            raise AssertionError

    return AgentNodes(
        intent_model=_Stub(),
        sufficiency_model=_Stub(),
        draft_model=_Stub(),
        embedder=_Stub(),
        retriever=_Stub(),
        scorer=_Stub(),
    )


def test_top_by_score_picks_max_and_handles_empty() -> None:
    assert _top_by_score([]) is None
    items = [{"doc_id": "a", "score": 0.01}, {"doc_id": "b", "score": 0.03}]
    assert _top_by_score(items)["doc_id"] == "b"
    assert _top_by_score(list(reversed(items)))["doc_id"] == "b"  # order-independent


def test_reset_validates_question_and_now() -> None:
    import datetime as dt

    nodes = _nodes()
    good_now = dt.datetime(2026, 8, 22, 12, 0, 0)
    with pytest.raises(ValueError, match="question is required"):
        nodes.reset({"now": good_now})
    with pytest.raises(ValueError, match="question is required"):
        nodes.reset({"question": "   ", "now": good_now})
    with pytest.raises(ValueError, match="now is required"):
        nodes.reset({"question": "q"})
    aware = dt.datetime(2026, 8, 22, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    with pytest.raises(ValueError, match="naive"):
        nodes.reset({"question": "q", "now": aware})


def test_reset_clears_per_question_control_fields() -> None:
    import datetime as dt

    fresh = _nodes().reset(
        {"question": "q", "now": dt.datetime(2026, 8, 22), "followup_count": 5, "declined_ids": [9]}
    )
    assert fresh["followup_count"] == 0
    assert fresh["declined_ids"] == []
    assert fresh["answer"] is None
    assert fresh["recommendations"] == []
    assert fresh["retrieval"]["candidate_people"] == []


# --------------------------------------------------------------------------- #
# C1 intent stub
# --------------------------------------------------------------------------- #
def test_intent_extracts_topics_products_and_type() -> None:
    result = KeywordIntentModel().analyze("VPNの拠点間接続の技術相談です", {"dept": "営業"})
    assert "ネットワーク・VPN" in result.topics
    assert "VPN" in result.products
    assert result.question_type == "技術相談"
    assert result.out_of_scope is False
    assert 0.0 < result.confidence <= 1.0


def test_intent_flags_out_of_scope() -> None:
    result = KeywordIntentModel().analyze("今日の天気を教えて", None)
    assert result.out_of_scope is True
    assert result.question_type == "業務外"
    assert result.confidence == 0.9


def test_intent_classifies_quote_admin_chitchat() -> None:
    model = KeywordIntentModel()
    assert model.analyze("この製品の見積をお願いします", None).question_type == "見積"
    assert model.analyze("経費精算の手続きについて", None).question_type == "事務手続き"
    assert model.analyze("こんにちは、よろしくお願いします", None).question_type == "雑談"
    # No signal at all -> plain product QA.
    assert model.analyze("これについて教えて", None).question_type == "製品QA"


# --------------------------------------------------------------------------- #
# C2 sufficiency stub
# --------------------------------------------------------------------------- #
def test_sufficiency_asks_once_for_missing_slots() -> None:
    # Topic-only question (no product, no 拠点) -> both slots missing.
    q = "ネットワークの技術相談です"
    intent = KeywordIntentModel().analyze(q, None)
    result = RuleSufficiencyModel().check(q, intent, followup_count=0)
    assert result.sufficient is False
    assert set(result.missing) == {"現行製品", "対象拠点数"}
    assert result.followup_question and "教えてください" in result.followup_question


def test_sufficiency_satisfied_when_slots_present() -> None:
    q = "現行のUTMから移行、対象は3拠点です"
    intent = KeywordIntentModel().analyze(q, None)
    result = RuleSufficiencyModel().check(q, intent, followup_count=0)
    assert result.sufficient is True
    assert result.missing == []


def test_sufficiency_proceeds_after_max_followups() -> None:
    intent = KeywordIntentModel().analyze("UTMの技術相談です", None)
    result = RuleSufficiencyModel().check("UTMの技術相談です", intent, followup_count=MAX_FOLLOWUPS)
    assert result.sufficient is True  # never ask more than once


def test_sufficiency_low_intent_asks_to_clarify() -> None:
    # Fix H: no topic extracted / low confidence -> treat as unclear intent and
    # ask to clarify, instead of proceeding to a dead-end no_candidate.
    q = "これについて教えて"
    intent = KeywordIntentModel().analyze(q, None)  # topics empty, low confidence
    result = RuleSufficiencyModel().check(q, intent, 0)
    assert result.sufficient is False
    assert result.missing == ["相談内容"]
    assert result.followup_question and "具体的" in result.followup_question


def test_sufficiency_keeps_unresolved_slots_after_cap() -> None:
    # Fix F: at the follow-up cap, proceed (sufficient) but KEEP unresolved slots
    # so C7 can flag them — do not silently clear missing.
    q = "ネットワークの技術相談です"  # both required slots missing
    intent = KeywordIntentModel().analyze(q, None)
    result = RuleSufficiencyModel().check(q, intent, MAX_FOLLOWUPS)
    assert result.sufficient is True
    assert set(result.missing) == {"現行製品", "対象拠点数"}


# --------------------------------------------------------------------------- #
# C7 draft stub
# --------------------------------------------------------------------------- #
def test_draft_is_polite_and_names_the_responder() -> None:
    draft = TemplateDraftModel().draft(
        "VPNの相談です", {"name": "高梨", "dept": "技術部"}, {"dept": "営業"}, ["現行製品"]
    )
    assert "高梨さん（技術部）" in draft
    assert "VPNの相談です" in draft
    assert "現行製品" in draft
    assert "お世話になっております" in draft


def test_draft_handles_missing_name_and_dept() -> None:
    draft = TemplateDraftModel().draft("質問です", {}, None, [])
    assert "ご担当者さん" in draft
    assert "補足" not in draft  # no missing slots -> no supplement section
