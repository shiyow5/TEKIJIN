"""Database-free unit tests for the agent's pure logic.

Covers the C5 route decision (all branches + tunable thresholds + determinism),
the conditional-edge routers, and the deterministic LLM stubs. No DB, no model,
no network.
"""

from __future__ import annotations

from tekijin.agent import graph as graph_mod
from tekijin.agent.route import (
    DOCUMENT,
    DOCUMENT_THRESHOLD,
    PERSON,
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
def test_route_person_is_the_default_with_candidates() -> None:
    r = _retrieval(
        answers=[{"qa_id": "a", "score": 0.005, "responder_id": 7}],
        documents=[{"doc_id": "d", "score": 0.001}],
        people=[1, 2, 3],
    )
    decision = decide_route(r)
    assert decision.route == PERSON
    assert decision.confidence >= 0.5  # PERSON_BASE_CONFIDENCE
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


def test_route_people_beat_document_even_above_doc_threshold() -> None:
    # A document over its bar must NOT demote when strong candidates exist.
    r = _retrieval(
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


def test_sufficiency_no_required_slots_for_plain_qa() -> None:
    intent = KeywordIntentModel().analyze("これについて教えて", None)  # 製品QA
    assert RuleSufficiencyModel().check("これについて教えて", intent, 0).sufficient is True


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
