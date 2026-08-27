"""Evidence stacking: turn a person's behavioural traces into a topic edge.

This is the heart of C6 (and the read side of the C8 graph): rather than trust a
stored ``person_topic_edges`` weight, the scorer recomputes it on the fly from
raw evidence — certifications, self-declared skills, project roles, and past
answers — each contributing its ``base_score`` (doc15). The functions are pure
(DTOs in, values out), so C8's future online update can reuse the exact same
aggregation.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from tekijin.data.dto import (
    AnswerDTO,
    CertificationDTO,
    DailyReportDTO,
    OfflineConsultDTO,
    ProjectMembershipDTO,
    SkillDTO,
)
from tekijin.scorer.features import saturate
from tekijin.scorer.topics import cert_matches_topic, product_matches_topic
from tekijin.scorer.weights import (
    BASE_SCORE_ANSWER,
    BASE_SCORE_CERTIFICATION,
    BASE_SCORE_DAILY,
    BASE_SCORE_HELPFUL_ANSWER,
    BASE_SCORE_OFFLINE_CONSULT,
    BASE_SCORE_PROJECT_LEAD,
    BASE_SCORE_PROJECT_MEMBER,
    BASE_SCORE_SKILL,
    DAILY_EVIDENCE_CAP,
    OFFLINE_CONSULT_EVIDENCE_CAP,
    OFFLINE_CONSULT_POSITIVE_RESOLUTIONS,
)


@dataclass(frozen=True, slots=True)
class Evidence:
    """One piece of evidence that a person knows a topic.

    ``source_type`` distinguishes provenance for the explanation: a self-declared
    skill (``self``) and an inferred one (``inferred``) share the same base_score
    but must not be described identically. A project's ``timestamp`` is its
    ``end_date`` — ``None`` while the project is still running (the scorer treats
    an ongoing project as current work for recency; see ``_recency_moments``).
    """

    source_type: str  # cert | self | inferred | project | answer | daily | offline_consult
    base_score: float
    timestamp: dt.date | dt.datetime | None
    detail: str


def collect_topic_evidence(
    topics: str | Sequence[str],
    certifications: Sequence[CertificationDTO],
    skills: Sequence[SkillDTO],
    memberships: Sequence[ProjectMembershipDTO],
    on_topic_answers: Sequence[AnswerDTO],
    daily_reports: Sequence[DailyReportDTO] = (),
    offline_consults: Sequence[OfflineConsultDTO] = (),
) -> list[Evidence]:
    """Assemble every piece of evidence a person has for ``topics``.

    ``topics`` may be a single topic (back-compatible) or several — for a
    multi-topic question the person's expertise across all of them is unioned.
    Each certification / skill / project contributes at most once even if it
    matches several topics (matched against the topic *set*), so nothing is
    double-counted. ``on_topic_answers`` must already be this person's answers for
    the topic set, de-duplicated by the caller (the topic *join* lives in the
    repository; the strict subtopic *filter* in the scorer). Ordering is
    deterministic: certs, skills, projects, answers, daily, in input order.

    ``daily_reports`` (#355) is this person's daily reports whose precomputed
    ``topics`` overlap the topic set. Off by default (empty) — the scorer passes
    them only when built with ``daily_evidence=True``. A single report is faint
    (``BASE_SCORE_DAILY``); at most ``DAILY_EVIDENCE_CAP`` on-topic reports count,
    so a prolific reporter cannot saturate ``topic_fit`` on shallow activity. This
    mirrors the eval gold, which sums daily activity at the same weight
    (``build_eval_v2.build_gold_evidence``); before #355 the scorer was blind to
    the very daily signal the gold rewards.

    ``offline_consults`` (#247) are 直接相談 retrospectives written by the ASKER
    about this person. Hearsay, so ``BASE_SCORE_OFFLINE_CONSULT`` sits below a
    self-declared skill; capped at ``OFFLINE_CONSULT_EVIDENCE_CAP`` for the same
    reason as daily reports. A retrospective whose ``resolution`` is not in
    ``OFFLINE_CONSULT_POSITIVE_RESOLUTIONS`` (i.e. 「解決しなかった」) contributes
    nothing and never subtracts — the decline rule (断り≠非専門) applied to
    consultations. Unlike daily reports this is NOT flag-gated: the rows only ever
    come from runtime submissions, so there is nothing in the fixtures to measure
    against and a dormant flag would just make the feature dead on arrival.
    """

    topic_set = {topics} if isinstance(topics, str) else set(topics)
    evidence: list[Evidence] = []

    for cert in certifications:
        if any(cert_matches_topic(cert.name, t) for t in topic_set):
            evidence.append(Evidence("cert", BASE_SCORE_CERTIFICATION, cert.acquired_at, cert.name))

    for skill in skills:
        if skill.topic in topic_set:
            # Preserve provenance: an inferred skill must not be called
            # "self-declared". A null source defaults to self-declared. The detail
            # carries the skill's OWN topic (accurate multi-topic reasons).
            source_type = "inferred" if skill.source == "inferred" else "self"
            evidence.append(Evidence(source_type, BASE_SCORE_SKILL, None, skill.topic))

    for member in memberships:
        if any(product_matches_topic(member.product, t) for t in topic_set):
            is_lead = member.role == "lead"
            base = BASE_SCORE_PROJECT_LEAD if is_lead else BASE_SCORE_PROJECT_MEMBER
            # end_date is None while the project is ongoing; the scorer maps that
            # to "now" for recency. base_score / topic_fit are unaffected.
            role_label = "リード" if is_lead else "メンバー"
            evidence.append(
                Evidence("project", base, member.end_date, f"{role_label}: {member.product}")
            )

    for answer in on_topic_answers:
        helpful = answer.was_helpful is True
        base = BASE_SCORE_HELPFUL_ANSWER if helpful else BASE_SCORE_ANSWER
        detail = "有用と評価された回答" if helpful else "過去の回答"
        evidence.append(Evidence("answer", base, answer.created_at, detail))

    # #355: daily reports whose precomputed topics overlap the set. Capped so
    # volume supports but does not dominate; deterministic (input order).
    daily_used = 0
    for report in daily_reports:
        if daily_used >= DAILY_EVIDENCE_CAP:
            break
        if topic_set & set(report.topics):
            daily_used += 1
            evidence.append(Evidence("daily", BASE_SCORE_DAILY, report.report_date, "日報での活動"))

    # #247: 直接相談のふりかえり. The resolution filter comes BEFORE the cap so an
    # unresolved consultation cannot consume a slot a helpful one would have used.
    consult_used = 0
    for consult in offline_consults:
        if consult_used >= OFFLINE_CONSULT_EVIDENCE_CAP:
            break
        if consult.resolution not in OFFLINE_CONSULT_POSITIVE_RESOLUTIONS:
            continue
        if topic_set & set(consult.topics):
            consult_used += 1
            detail = (
                "直接相談で解決" if consult.resolution == "resolved" else "直接相談で部分的に解決"
            )
            evidence.append(
                Evidence("offline_consult", BASE_SCORE_OFFLINE_CONSULT, consult.created_at, detail)
            )

    return evidence


def edge_weight(evidence: Sequence[Evidence]) -> float:
    """Saturated sum of base_scores — the person↔topic edge weight in ``[0, 1)``."""

    return saturate(sum(e.base_score for e in evidence))
