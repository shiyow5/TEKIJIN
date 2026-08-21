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
    DOCUMENT_SIM,
    PERSON,
    PERSON_WEAK_SIM,
    PRIOR_ANSWER,
    PRIOR_ANSWER_SIM,
    decide_route,
)
from tekijin.agent.stubs import (
    MAX_FOLLOWUPS,
    KeywordIntentModel,
    RuleSufficiencyModel,
    TemplateDraftModel,
)


def _retrieval(*, answer=0.0, document=0.0, people_sim=0.0, people=(), past_answers=None) -> dict:
    # C5 routes on the absolute cosine similarities (*_confidence), not RRF scores.
    # ``past_answers`` defaults to one entry whenever answer confidence is set, so
    # the prior_answer gate (needs actual past answers) is satisfied by default.
    if past_answers is None:
        past_answers = [{"qa_id": "a", "score": 0.01, "responder_id": 7}] if answer else []
    return {
        "past_answers": list(past_answers),
        "documents": [],
        "candidate_people": list(people),
        "answer_confidence": answer,
        "document_confidence": document,
        "people_confidence": people_sim,
    }


# --------------------------------------------------------------------------- #
# C5 route decision (on absolute cosine similarity)
# --------------------------------------------------------------------------- #
def test_route_prior_answer_when_answer_is_near_duplicate() -> None:
    r = _retrieval(answer=0.90, people_sim=0.95, people=[1, 2])
    decision = decide_route(r)
    assert decision.route == PRIOR_ANSWER
    assert decision.confidence == pytest.approx(0.90)
    assert "過去QA" in decision.reason


def test_route_prior_answer_at_exact_threshold() -> None:
    # Boundary: exactly at the bar counts (>=).
    assert decide_route(_retrieval(answer=PRIOR_ANSWER_SIM, people=[1])).route == PRIOR_ANSWER
    just_below = _retrieval(answer=PRIOR_ANSWER_SIM - 0.001, people=[1])
    assert decide_route(just_below).route == PERSON


def test_route_no_prior_answer_without_past_answers() -> None:
    # Fix 2: a near-duplicate *question* with NO past answers must NOT route to
    # prior_answer (nobody to hand off to) — falls back to person.
    r = _retrieval(answer=0.95, people=[1], past_answers=[])
    assert decide_route(r).route == PERSON


def test_route_document_when_person_signal_weak() -> None:
    # No near-duplicate answer, weak profile match, strong document -> document,
    # even though candidate people are present (Fix A: was dead before).
    r = _retrieval(answer=0.10, document=0.75, people_sim=0.30, people=[1, 2, 3])
    decision = decide_route(r)
    assert decision.route == DOCUMENT
    assert decision.confidence == pytest.approx(0.75)
    assert "文書" in decision.reason


def test_route_document_at_exact_thresholds() -> None:
    # document_confidence == DOCUMENT_SIM and people_confidence just below the weak
    # bar -> document; people_confidence == PERSON_WEAK_SIM (not weak) -> person.
    at_bar = _retrieval(document=DOCUMENT_SIM, people_sim=PERSON_WEAK_SIM - 0.001, people=[1])
    assert decide_route(at_bar).route == DOCUMENT
    people_ok = _retrieval(document=DOCUMENT_SIM, people_sim=PERSON_WEAK_SIM, people=[1])
    assert decide_route(people_ok).route == PERSON


def test_route_person_when_profile_match_is_strong() -> None:
    # A strong profile match keeps the person route even with a strong document.
    r = _retrieval(answer=0.10, document=0.85, people_sim=0.80, people=[1, 2, 3])
    decision = decide_route(r)
    assert decision.route == PERSON
    assert decision.confidence == pytest.approx(0.80)  # max(people, answer)
    assert "主線" in decision.reason


def test_route_person_fallback_when_nothing() -> None:
    decision = decide_route(_retrieval())
    assert decision.route == PERSON
    assert decision.confidence == 0.0


def test_route_thresholds_are_tunable() -> None:
    r = _retrieval(answer=0.82, people=[1])
    # Raise the prior_answer bar above the confidence -> falls back to person.
    assert decide_route(r, prior_answer_sim=0.95).route == PERSON


def test_route_missing_confidence_keys_default_to_zero() -> None:
    # A retriever that omits the confidence fields -> all-zero -> person.
    assert decide_route({"candidate_people": [1]}).route == PERSON


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


def test_intent_covers_all_22_canonical_topics() -> None:
    from tekijin.agent.stubs import TOPIC_KEYWORDS

    # Fix 2: the stub can extract every one of the 22 canonical topics — and the
    # keys match the scorer's exact-topic join (skills.topic) verbatim.
    assert len(TOPIC_KEYWORDS) == 22
    model = KeywordIntentModel()
    # Spot-check topics that were missing before (EC / SNS / mobile / 基幹 …).
    assert "ECサイト構築" in model.analyze("ECサイトの構築を相談したい", None).topics
    assert "SNS運用" in model.analyze("SNSの運用代行について", None).topics
    assert "モバイルアプリ開発" in model.analyze("スマホアプリの開発の件", None).topics
    assert "基幹システム" in model.analyze("基幹システムのERP刷新", None).topics
    assert "購買・仕入れ" in model.analyze("調達と仕入れの相談です", None).topics
    assert "総務・法務" in model.analyze("契約審査など法務の相談", None).topics


def test_intent_flags_out_of_scope() -> None:
    result = KeywordIntentModel().analyze("今日の天気を教えて", None)
    assert result.out_of_scope is True
    assert result.question_type == "業務外"
    assert result.confidence == 0.9


def test_chitchat_is_clarified_not_deflected() -> None:
    # Design decision (Fix 7): a bare greeting is low-signal but in-scope for a
    # work helpdesk, so C1 does NOT mark it out_of_scope, and C2 asks to clarify
    # (rather than deflecting like genuine off-topic input).
    intent = KeywordIntentModel().analyze("こんにちは", None)
    assert intent.out_of_scope is False
    result = RuleSufficiencyModel().check("こんにちは", intent, 0)
    assert result.sufficient is False
    assert "具体的" in (result.followup_question or "")


def test_intent_classifies_quote_admin_chitchat() -> None:
    model = KeywordIntentModel()
    assert model.analyze("この製品の見積をお願いします", None).question_type == "見積"
    assert model.analyze("経費精算の手続きについて", None).question_type == "事務手続き"
    assert model.analyze("こんにちは、よろしくお願いします", None).question_type == "雑談"
    # No signal at all -> plain product QA.
    assert model.analyze("これについて教えて", None).question_type == "製品QA"


def test_intent_short_abbrev_matches_on_word_boundary() -> None:
    # Fix 1: short ASCII abbreviations must not fire inside English words.
    model = KeywordIntentModel()
    for false_positive in (
        "security",  # would wrongly match "ec"
        "please review this project",  # "pr" / "ec"
        "how do I connect the cable",  # "ec"
        "improve the process",  # "pr"
        "ability check",  # "bi"
    ):
        result = model.analyze(false_positive, None)
        assert result.topics == []
        assert result.products == []
    # But a genuine EC question still hits (boundary via the Japanese char).
    assert "ECサイト構築" in model.analyze("ECサイトを構築したい", None).topics
    assert "EC" in model.analyze("現行のECで相談", None).products


def test_greeting_with_topic_is_technical_consult() -> None:
    # Fix 4: a concrete topic overrides the greeting classification, so the
    # substantive request is not skipped as chitchat.
    result = KeywordIntentModel().analyze("こんにちは、ネットワークの技術相談です", None)
    assert result.question_type == "技術相談"
    assert "ネットワーク・VPN" in result.topics


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


def test_sufficiency_requires_slot_values_not_labels() -> None:
    # Fix 6: bare labels ("現行環境", "拠点間") without a product value or a site
    # COUNT do not satisfy the slots -> still asks for clarification.
    q = "現行環境と拠点間ネットワークの技術相談です"
    intent = KeywordIntentModel().analyze(q, None)
    assert intent.products == []  # no known product value mentioned
    result = RuleSufficiencyModel().check(q, intent, followup_count=0)
    assert result.sufficient is False
    assert set(result.missing) == {"現行製品", "対象拠点数"}


def test_sufficiency_site_count_needs_a_number() -> None:
    # "拠点" without a number is not a count; "3拠点" is.
    assert RuleSufficiencyModel._slot_present("対象拠点数", "拠点間の相談", None) is False
    assert RuleSufficiencyModel._slot_present("対象拠点数", "対象は5拠点です", None) is True


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
