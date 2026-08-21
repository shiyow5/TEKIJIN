"""C6 expertise scorer: rank candidate people with explainable reasons.

Deterministic, LLM-free. Given a question topic and the candidate people C4
retrieved, it recomputes each person's topic edge from raw evidence and combines
it with recency, answer quality, proximity, and a load penalty into a single
score (technical-spec §5). It returns the top-k with a per-term ``reasons``
breakdown — the explanation the UI shows and the project's answer to "誤推薦対策".

``now`` is injected (never read from the clock) so the load window and recency
are reproducible; ties break on ``person_id`` so the ranking is stable.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from tekijin.data.dto import AnswerDTO
from tekijin.data.repository import Repository
from tekijin.scorer.evidence import Evidence, collect_topic_evidence, edge_weight
from tekijin.scorer.features import (
    answer_quality,
    confidence_label,
    load,
    proximity,
    recency,
)
from tekijin.scorer.weights import (
    DAYS_PER_MONTH,
    DEFAULT_WEIGHTS,
    LOAD_WINDOW_DAYS,
    REGION_OF_BRANCH,
    Weights,
)

# Evidence types that decay with time. Certifications are excluded: a
# qualification does not become less true as it ages, so it must not drag the
# recency term down (technical-spec §5 ties recency to projects/answers).
_RECENCY_SOURCE_TYPES = ("project", "answer")

# Fixed order to break reason-contribution ties deterministically.
_REASON_TYPE_ORDER = {
    "cert": 0,
    "self": 1,
    "answers": 2,
    "project": 3,
    "recency": 4,
    "proximity": 5,
    "load": 6,
}


class ExpertiseScorer:
    """Ranks candidate people for a topic from behavioural evidence."""

    def __init__(self, repository: Repository, *, weights: Weights = DEFAULT_WEIGHTS) -> None:
        self._repo = repository
        self._weights = weights

    def rank(
        self,
        topic: str,
        candidate_ids: Sequence[int],
        asker_id: int | None,
        now: dt.datetime,
        *,
        top_k: int = 3,
    ) -> dict[str, Any]:
        """Return ``{"recommendations": [...]}`` — the top-``top_k`` candidates.

        Only ids in ``candidate_ids`` that resolve to a real employee are scored;
        unknown ids are dropped (never fabricated). The asker (``asker_id``) is
        removed from the candidates — never recommend a person to themselves.

        ``now`` must be timezone-naive: stored ``created_at`` values are naive, so
        an aware ``now`` would raise on comparison/subtraction.
        """

        if top_k <= 0:
            raise ValueError(f"top_k must be positive, got {top_k}")
        assert now.tzinfo is None, "now must be naive (matches stored timestamps)"

        # De-dupe (keep first order), then drop the asker (only when known).
        candidates = [
            pid for pid in dict.fromkeys(candidate_ids) if asker_id is None or pid != asker_id
        ]

        since = now - dt.timedelta(days=LOAD_WINDOW_DAYS)
        rec_counts = self._repo.recent_recommendation_counts(since, candidates)
        ans_counts = self._repo.recent_answer_counts(since, candidates)
        answers_by_person = self._group_answers_by_person(self._topic_answers(topic))
        asker_branch = self._asker_branch(asker_id)

        scored: list[tuple[float, int, dict[str, Any]]] = []
        for person_id in candidates:
            employee = self._repo.get_employee(person_id)
            if employee is None:
                continue
            record, score = self._score_person(
                topic=topic,
                employee_id=person_id,
                employee_name=employee.name,
                employee_dept=employee.department,
                employee_branch=employee.branch,
                answers=answers_by_person.get(person_id, []),
                load_count=rec_counts.get(person_id, 0) + ans_counts.get(person_id, 0),
                asker_branch=asker_branch,
                now=now,
            )
            scored.append((score, person_id, record))

        # Descending score, then ascending person_id for a stable tiebreak.
        scored.sort(key=lambda item: (-item[0], item[1]))
        return {"recommendations": [record for _, _, record in scored[:top_k]]}

    def _topic_answers(self, topic: str) -> list[AnswerDTO]:
        """Answers that are genuine evidence for ``topic``.

        ``answers_by_topic`` also returns answers matched only via their
        question's ``topics`` array. An answer that carries its OWN ``topic`` for
        a *different* subtopic must not count as this topic's evidence, so keep an
        answer only when ``answer.topic == topic`` or ``answer.topic`` is unset
        (in which case the question-topics fallback is the intended signal).
        """

        return [
            a for a in self._repo.answers_by_topic(topic) if a.topic == topic or a.topic is None
        ]

    # ------------------------------------------------------------------ #
    # per-person scoring
    # ------------------------------------------------------------------ #
    def _score_person(
        self,
        *,
        topic: str,
        employee_id: int,
        employee_name: str,
        employee_dept: str | None,
        employee_branch: str | None,
        answers: Sequence[AnswerDTO],
        load_count: int,
        asker_branch: str | None,
        now: dt.datetime,
    ) -> tuple[dict[str, Any], float]:
        evidence = collect_topic_evidence(
            topic,
            self._repo.certifications_for(employee_id),
            self._repo.skills_for(employee_id),
            self._repo.project_memberships_for(employee_id),
            answers,
        )
        weights = self._weights

        topic_fit = edge_weight(evidence)
        recency_score = recency(now, self._recency_moments(evidence))
        helpful_count = sum(1 for a in answers if a.was_helpful is True)
        reuse_total = sum(a.reuse_count or 0 for a in answers)
        quality = answer_quality(helpful_count, reuse_total, len(answers))
        proximity_score = proximity(asker_branch, employee_branch)
        load_score = load(load_count)

        score = (
            weights.topic_fit * topic_fit
            + weights.recency * recency_score
            + weights.answer_quality * quality
            + weights.proximity * proximity_score
            - weights.load * load_score
        )

        reasons = self._build_reasons(
            topic=topic,
            evidence=evidence,
            topic_fit=topic_fit,
            recency_score=recency_score,
            quality=quality,
            proximity_score=proximity_score,
            load_score=load_score,
            load_count=load_count,
            answers=answers,
            helpful_count=helpful_count,
            employee_branch=employee_branch,
            asker_branch=asker_branch,
            now=now,
        )
        record = {
            "person_id": employee_id,
            "name": employee_name,
            "dept": employee_dept,
            "score": round(score, 4),
            "confidence": confidence_label(topic_fit, len(evidence)),
            "reasons": reasons,
        }
        return record, score

    # ------------------------------------------------------------------ #
    # reasons
    # ------------------------------------------------------------------ #
    def _build_reasons(
        self,
        *,
        topic: str,
        evidence: Sequence[Evidence],
        topic_fit: float,
        recency_score: float,
        quality: float,
        proximity_score: float,
        load_score: float,
        load_count: int,
        answers: Sequence[AnswerDTO],
        helpful_count: int,
        employee_branch: str | None,
        asker_branch: str | None,
        now: dt.datetime,
    ) -> list[dict[str, str]]:
        weights = self._weights
        base_total = sum(e.base_score for e in evidence)
        entries: list[tuple[float, dict[str, str]]] = []

        def topic_fit_share(source_types: tuple[str, ...]) -> float:
            # Only ever called from a block guarded by matching evidence, so
            # ``base_total`` is strictly positive here.
            share = sum(e.base_score for e in evidence if e.source_type in source_types)
            return weights.topic_fit * topic_fit * (share / base_total)

        cert_names = [e.detail for e in evidence if e.source_type == "cert"]
        if cert_names:
            entries.append(
                (topic_fit_share(("cert",)), {"type": "cert", "detail": "、".join(cert_names)})
            )

        # Self-declared skill: surface it so cold-start candidates (chosen on a
        # skill alone) are explained by their expertise signal, not just load.
        if any(e.source_type == "self" for e in evidence):
            entries.append(
                (
                    topic_fit_share(("self",)),
                    {"type": "self", "detail": f"自己申告スキル: {topic}"},
                )
            )

        if answers:
            detail = f"類似の質問に過去{len(answers)}件回答"
            if helpful_count:
                detail += f"（うち有用と評価{helpful_count}件）"
            answers_weight = topic_fit_share(("answer",)) + weights.answer_quality * quality
            entries.append((answers_weight, {"type": "answers", "detail": detail}))

        project_count = sum(1 for e in evidence if e.source_type == "project")
        if project_count:
            entries.append(
                (
                    topic_fit_share(("project",)),
                    {"type": "project", "detail": f"同種の案件を{project_count}件担当"},
                )
            )

        recency_detail = self._recency_detail(evidence, now) if recency_score > 0.0 else ""
        if recency_detail:
            entries.append(
                (weights.recency * recency_score, {"type": "recency", "detail": recency_detail})
            )

        entries.append(
            (
                weights.proximity * proximity_score,
                {
                    "type": "proximity",
                    "detail": self._proximity_detail(asker_branch, employee_branch),
                },
            )
        )
        entries.append(
            (
                -weights.load * load_score,
                {"type": "load", "detail": f"今週の対応件数: {self._load_label(load_count)}"},
            )
        )

        # Highest contribution first; stable tiebreak on the fixed type order.
        entries.sort(key=lambda item: (-item[0], _REASON_TYPE_ORDER[item[1]["type"]]))
        return [reason for _, reason in entries]

    @staticmethod
    def _recency_moments(evidence: Sequence[Evidence]) -> list[dt.date | dt.datetime]:
        """Timestamps of the time-decaying evidence (projects/answers).

        Certifications are omitted on purpose — a qualification does not fade
        with age, so it must not feed the recency term (see _RECENCY_SOURCE_TYPES).
        """

        return [
            e.timestamp
            for e in evidence
            if e.timestamp is not None and e.source_type in _RECENCY_SOURCE_TYPES
        ]

    @classmethod
    def _recency_detail(cls, evidence: Sequence[Evidence], now: dt.datetime) -> str:
        moments = cls._recency_moments(evidence)
        if not moments:
            return ""
        newest = max(
            (m if isinstance(m, dt.datetime) else dt.datetime(m.year, m.month, m.day))
            for m in moments
        )
        months = max((now - newest).total_seconds() / 86400.0 / DAYS_PER_MONTH, 0.0)
        if months < 1.0:
            return "直近1か月以内の関連実績あり"
        return f"直近の関連実績: 約{round(months)}か月前"

    @staticmethod
    def _proximity_detail(asker_branch: str | None, employee_branch: str | None) -> str:
        if asker_branch and employee_branch and asker_branch == employee_branch:
            return f"同じ拠点（{employee_branch}）"
        if (
            asker_branch
            and employee_branch
            and REGION_OF_BRANCH.get(asker_branch) is not None
            and REGION_OF_BRANCH.get(asker_branch) == REGION_OF_BRANCH.get(employee_branch)
        ):
            return "同じエリア"
        return "全社から選定"

    @staticmethod
    def _load_label(load_count: int) -> str:
        if load_count <= 1:
            return "少なめ"
        if load_count <= 4:
            return "やや多め"
        return "多め"

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _group_answers_by_person(answers: Sequence[AnswerDTO]) -> dict[int, list[AnswerDTO]]:
        grouped: dict[int, list[AnswerDTO]] = {}
        for answer in answers:
            grouped.setdefault(answer.responder_id, []).append(answer)
        return grouped

    def _asker_branch(self, asker_id: int | None) -> str | None:
        if asker_id is None:
            return None
        asker = self._repo.get_employee(asker_id)
        return asker.branch if asker is not None else None
