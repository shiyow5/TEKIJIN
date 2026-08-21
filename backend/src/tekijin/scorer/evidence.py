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
    """One piece of evidence that a person knows a topic."""

    source_type: str  # cert | self | project | answer
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

    ``on_topic_answers`` must already be this person's answers on the topic (the
    topic join lives in the repository); certifications/skills/projects are
    matched here. Ordering is deterministic: certs, skills, projects, answers.
    """

    evidence: list[Evidence] = []

    for cert in certifications:
        if cert_matches_topic(cert.name, topic):
            evidence.append(Evidence("cert", BASE_SCORE_CERTIFICATION, cert.acquired_at, cert.name))

    for skill in skills:
        if skill.topic == topic:
            detail = f"自己申告スキル（{skill.level}）" if skill.level else "自己申告スキル"
            evidence.append(Evidence("self", BASE_SCORE_SKILL, None, detail))

    for member in memberships:
        if product_matches_topic(member.product, topic):
            is_lead = member.role == "lead"
            base = BASE_SCORE_PROJECT_LEAD if is_lead else BASE_SCORE_PROJECT_MEMBER
            when = member.end_date or member.start_date
            role_label = "リード" if is_lead else "メンバー"
            evidence.append(Evidence("project", base, when, f"{role_label}: {member.product}"))

    for answer in on_topic_answers:
        helpful = answer.was_helpful is True
        base = BASE_SCORE_HELPFUL_ANSWER if helpful else BASE_SCORE_ANSWER
        detail = "有用と評価された回答" if helpful else "過去の回答"
        evidence.append(Evidence("answer", base, answer.created_at, detail))

    return evidence


def edge_weight(evidence: Sequence[Evidence]) -> float:
    """Saturated sum of base_scores — the person↔topic edge weight in ``[0, 1)``."""

    return saturate(sum(e.base_score for e in evidence))
