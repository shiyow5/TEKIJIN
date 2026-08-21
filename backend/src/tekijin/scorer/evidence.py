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

from tekijin.data.dto import AnswerDTO, CertificationDTO, ProjectMembershipDTO, SkillDTO
from tekijin.scorer.features import saturate
from tekijin.scorer.topics import cert_matches_topic, product_matches_topic
from tekijin.scorer.weights import (
    BASE_SCORE_ANSWER,
    BASE_SCORE_CERTIFICATION,
    BASE_SCORE_HELPFUL_ANSWER,
    BASE_SCORE_PROJECT_LEAD,
    BASE_SCORE_PROJECT_MEMBER,
    BASE_SCORE_SKILL,
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

    source_type: str  # cert | self | inferred | project | answer
    base_score: float
    timestamp: dt.date | dt.datetime | None
    detail: str


def collect_topic_evidence(
    topic: str,
    certifications: Sequence[CertificationDTO],
    skills: Sequence[SkillDTO],
    memberships: Sequence[ProjectMembershipDTO],
    on_topic_answers: Sequence[AnswerDTO],
) -> list[Evidence]:
    """Assemble every piece of evidence a person has for ``topic``.

    ``on_topic_answers`` must already be this person's answers on the topic: the
    topic *join* lives in the repository (``answers_by_topic``) and the strict
    subtopic *filter* in the scorer (``_topic_answers``). Certifications, skills,
    and projects are matched here. Ordering is deterministic: certs, skills,
    projects, answers.
    """

    evidence: list[Evidence] = []

    for cert in certifications:
        if cert_matches_topic(cert.name, topic):
            evidence.append(Evidence("cert", BASE_SCORE_CERTIFICATION, cert.acquired_at, cert.name))

    for skill in skills:
        if skill.topic == topic:
            # Preserve provenance: an inferred skill must not be called
            # "self-declared". A null source defaults to self-declared.
            if skill.source == "inferred":
                evidence.append(
                    Evidence("inferred", BASE_SCORE_SKILL, None, f"推定スキル: {topic}")
                )
            else:
                evidence.append(
                    Evidence("self", BASE_SCORE_SKILL, None, f"自己申告スキル: {topic}")
                )

    for member in memberships:
        if product_matches_topic(member.product, topic):
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

    return evidence


def edge_weight(evidence: Sequence[Evidence]) -> float:
    """Saturated sum of base_scores — the person↔topic edge weight in ``[0, 1)``."""

    return saturate(sum(e.base_score for e in evidence))
