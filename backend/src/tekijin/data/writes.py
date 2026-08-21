"""Write-side persistence for the API flow (questions, recommendations, outcomes).

Kept apart from the read-only :mod:`tekijin.data.repository`. Each function takes
an active session and mutates it; the caller owns the transaction (``session_scope``).
The API persists so ``load`` (recent recommendations) and the dashboard reflect
real usage — the "使うほど育つ" loop. (The C8 person_topic_edges write stays a
separate issue.)
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from tekijin.models.tables import Question, Recommendation


def persist_question(
    session: Session,
    question_id: str,
    asker_id: int,
    body: str,
    now: dt.datetime,
) -> None:
    """Insert the asked question (``created_at`` from the run's injected ``now``)."""

    session.add(
        Question(
            id=question_id,
            asker_id=asker_id,
            body=body,
            topics=[],
            status="open",
            created_at=now,
        )
    )


def update_question_topics(session: Session, question_id: str, topics: list[str]) -> None:
    """Backfill C1's extracted topics onto the question (for the dashboard mix)."""

    session.execute(update(Question).where(Question.id == question_id).values(topics=list(topics)))


def insert_recommendation(
    session: Session,
    question_id: str,
    employee_id: int,
    *,
    rank: int,
    score: float | None,
    reasons: list[dict[str, Any]],
    now: dt.datetime,
) -> int:
    """Insert one recommendation row and return its generated id."""

    rec = Recommendation(
        question_id=question_id,
        employee_id=employee_id,
        rank=rank,
        score=score,
        reasons={"reasons": reasons},  # JSONB column is typed as a dict
        outcome=None,
        created_at=now,
    )
    session.add(rec)
    session.flush()
    session.refresh(rec)
    return rec.id


def set_recommendation_outcome(session: Session, recommendation_id: int, outcome: str) -> None:
    """Record the responder's accept/decline on a recommendation."""

    session.execute(
        update(Recommendation).where(Recommendation.id == recommendation_id).values(outcome=outcome)
    )
