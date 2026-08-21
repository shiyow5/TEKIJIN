"""Database-free unit tests for the C6 scorer's pure logic.

Covers evidence stacking -> edge weight, the time-decayed recency (with an
injected ``now``), the load window saturation, proximity tiers, answer quality,
confidence derivation, and topic matching. Every value is deterministic.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tekijin.data.dto import AnswerDTO, CertificationDTO, ProjectMembershipDTO, SkillDTO
from tekijin.scorer import features
from tekijin.scorer.evidence import Evidence, collect_topic_evidence, edge_weight
from tekijin.scorer.scorer import ExpertiseScorer
from tekijin.scorer.topics import cert_matches_topic, product_matches_topic
from tekijin.scorer.weights import (
    BASE_SCORE_CERTIFICATION,
    BASE_SCORE_HELPFUL_ANSWER,
    BASE_SCORE_PROJECT_LEAD,
    BASE_SCORE_PROJECT_MEMBER,
    BASE_SCORE_SKILL,
)

NOW = dt.datetime(2026, 8, 21, 12, 0, 0)
TOPIC = "ネットワーク・VPN"


# --------------------------------------------------------------------------- #
# helpers to build DTOs
# --------------------------------------------------------------------------- #
def _cert(name: str, acquired: dt.date | None = None) -> CertificationDTO:
    return CertificationDTO(id="c", employee_id=1, name=name, acquired_at=acquired)


def _skill(topic: str, level: str | None = "中級") -> SkillDTO:
    return SkillDTO(id="s", employee_id=1, topic=topic, level=level, source="self")


def _membership(product: str, role: str, end: dt.date | None = None) -> ProjectMembershipDTO:
    return ProjectMembershipDTO(
        project_id=1,
        employee_id=1,
        role=role,
        product=product,
        industry=None,
        subject=None,
        status="受注",
        start_date=dt.date(2026, 1, 1),
        end_date=end,
    )


def _answer(helpful: bool | None, reuse: int | None, created: dt.datetime) -> AnswerDTO:
    return AnswerDTO(
        id="a",
        question_id="q",
        responder_id=1,
        body="b",
        topic=TOPIC,
        reuse_count=reuse,
        was_helpful=helpful,
        created_at=created,
        has_embedding=False,
    )


# --------------------------------------------------------------------------- #
# topic matching
# --------------------------------------------------------------------------- #
def test_cert_matches_topic_by_substring() -> None:
    assert cert_matches_topic("情報処理安全確保支援士", "セキュリティ") is True
    assert cert_matches_topic("ネットワークスペシャリスト", "ネットワーク・VPN") is True
    assert cert_matches_topic("日商簿記1級", "セキュリティ") is False
    assert cert_matches_topic(None, "セキュリティ") is False


def test_product_matches_topic() -> None:
    assert product_matches_topic("CRM導入支援", "CRM・営業支援") is True
    assert product_matches_topic("広告運用代行", "Webマーケティング・広告") is True
    assert product_matches_topic("CRM導入支援", "セキュリティ") is False
    assert product_matches_topic(None, "CRM・営業支援") is False


# --------------------------------------------------------------------------- #
# evidence -> edge weight
# --------------------------------------------------------------------------- #
def test_collect_topic_evidence_assigns_base_scores() -> None:
    evidence = collect_topic_evidence(
        TOPIC,
        [_cert("ネットワークスペシャリスト"), _cert("日商簿記1級")],  # only the first matches
        [_skill(TOPIC), _skill("経理・決算")],  # only the first matches
        [_membership("保守運用サポート", "lead")],  # product -> サーバー…, NOT this topic
        [_answer(True, 3, NOW), _answer(False, 0, NOW)],
    )
    kinds = [(e.source_type, e.base_score) for e in evidence]
    assert ("cert", BASE_SCORE_CERTIFICATION) in kinds
    assert ("self", BASE_SCORE_SKILL) in kinds
    assert ("answer", BASE_SCORE_HELPFUL_ANSWER) in kinds
    # The non-matching cert / skill / project produced no evidence.
    assert sum(1 for e in evidence if e.source_type == "cert") == 1
    assert sum(1 for e in evidence if e.source_type == "self") == 1
    assert sum(1 for e in evidence if e.source_type == "project") == 0


def test_collect_topic_evidence_project_role_base_scores() -> None:
    lead = collect_topic_evidence("CRM・営業支援", [], [], [_membership("CRM導入支援", "lead")], [])
    member = collect_topic_evidence(
        "CRM・営業支援", [], [], [_membership("CRM導入支援", "member")], []
    )
    assert lead[0].base_score == BASE_SCORE_PROJECT_LEAD
    assert member[0].base_score == BASE_SCORE_PROJECT_MEMBER


def test_edge_weight_saturates_and_is_monotonic() -> None:
    small = edge_weight([Evidence("self", BASE_SCORE_SKILL, None, "x")])
    big = edge_weight(
        [
            Evidence("answer", BASE_SCORE_HELPFUL_ANSWER, None, "x"),
            Evidence("cert", BASE_SCORE_CERTIFICATION, None, "x"),
            Evidence("project", BASE_SCORE_PROJECT_LEAD, None, "x"),
        ]
    )
    assert 0.0 < small < big < 1.0
    assert edge_weight([]) == 0.0


# --------------------------------------------------------------------------- #
# recency (now injected)
# --------------------------------------------------------------------------- #
def test_recency_half_life() -> None:
    six_months_ago = NOW - dt.timedelta(days=182)  # ~ half-life
    assert features.recency(NOW, [six_months_ago]) == pytest.approx(0.5, abs=0.02)


def test_recency_picks_most_recent_and_handles_empty() -> None:
    old = NOW - dt.timedelta(days=365)
    fresh = NOW - dt.timedelta(days=1)
    assert features.recency(NOW, [old, fresh]) > features.recency(NOW, [old])
    assert features.recency(NOW, []) == 0.0


def test_recency_accepts_date_and_clamps_future() -> None:
    # A plain date is treated as midnight; a future date does not exceed 1.0.
    assert features.recency(NOW, [dt.date(2026, 8, 21)]) == pytest.approx(1.0, abs=0.01)
    future = NOW + dt.timedelta(days=30)
    assert features.recency(NOW, [future]) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# load / answer_quality / proximity / confidence
# --------------------------------------------------------------------------- #
def test_load_monotonic_and_half_saturation() -> None:
    assert features.load(0) == 0.0
    assert features.load(5) == pytest.approx(0.5)
    assert features.load(20) > features.load(5)
    # Realistic weekly counts stay below the cap (extreme counts float-saturate).
    assert features.load(15) < 1.0


def test_answer_quality_monotonic() -> None:
    none = features.answer_quality(0, 0, 0)
    some = features.answer_quality(0, 0, 2)
    helpful = features.answer_quality(2, 0, 2)
    reused = features.answer_quality(2, 10, 2)
    assert none == 0.0
    assert 0.0 < some < helpful < reused < 1.0


def test_proximity_tiers() -> None:
    assert features.proximity("大阪", "大阪") == 1.0
    assert features.proximity("東京", "本社") == 0.5  # same 関東 region
    assert features.proximity("大阪", "東京") == 0.2
    assert features.proximity(None, "大阪") == 0.2
    assert features.proximity("大阪", None) == 0.2


def test_confidence_label_thresholds() -> None:
    assert features.confidence_label(0.8, 3) == "高"
    assert features.confidence_label(0.5, 1) == "中"
    assert features.confidence_label(0.2, 2) == "中"  # evidence_count path
    assert features.confidence_label(0.1, 1) == "低"


# --------------------------------------------------------------------------- #
# reason-detail helpers (static, DB-free)
# --------------------------------------------------------------------------- #
def test_recency_detail_empty_and_months() -> None:
    assert ExpertiseScorer._recency_detail([], NOW) == ""
    # Skills carry no timestamp -> still empty.
    assert ExpertiseScorer._recency_detail([Evidence("self", 0.3, None, "x")], NOW) == ""
    recent = ExpertiseScorer._recency_detail(
        [Evidence("answer", 0.7, NOW - dt.timedelta(days=5), "x")], NOW
    )
    assert recent == "直近1か月以内の関連実績あり"
    older = ExpertiseScorer._recency_detail(
        [Evidence("project", 0.8, NOW - dt.timedelta(days=95), "x")], NOW
    )
    assert older == "直近の関連実績: 約3か月前"


def test_proximity_detail_tiers() -> None:
    assert ExpertiseScorer._proximity_detail("大阪", "大阪") == "同じ拠点（大阪）"
    assert ExpertiseScorer._proximity_detail("東京", "本社") == "同じエリア"  # same 関東 region
    assert ExpertiseScorer._proximity_detail("大阪", "東京") == "全社から選定"
    assert ExpertiseScorer._proximity_detail(None, "大阪") == "全社から選定"


def test_load_label_bands() -> None:
    assert ExpertiseScorer._load_label(0) == "少なめ"
    assert ExpertiseScorer._load_label(3) == "やや多め"
    assert ExpertiseScorer._load_label(9) == "多め"
