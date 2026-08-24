"""Read-only lookups for a question's post-acceptance chat thread (#E6).

A message thread has exactly two participants: the asker and the responder who
accepted the hand-off. Both are derived from existing rows (``Question.asker_id``
and the accepted ``Recommendation``) rather than duplicated onto ``Message``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import Message, Question, Recommendation


def resolve_question_id(session: Session, session_id: str) -> str | None:
    """The question id for a live ``session_id``, or ``None`` if unknown."""

    return session.execute(
        select(Question.id).where(Question.session_id == session_id)
    ).scalar_one_or_none()


def question_participants(session: Session, question_id: str) -> tuple[int | None, int | None]:
    """``(asker_id, accepted_responder_id)`` for a question; either may be ``None``.

    ``accepted_responder_id`` is ``None`` until a responder has accepted (a
    thread is not open before that) — the newest accepted row wins if more than
    one somehow exists.
    """

    asker_id = session.execute(
        select(Question.asker_id).where(Question.id == question_id)
    ).scalar_one_or_none()
    responder_id = session.execute(
        select(Recommendation.employee_id)
        .where(Recommendation.question_id == question_id, Recommendation.outcome == "accepted")
        .order_by(Recommendation.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return asker_id, responder_id


def list_messages(session: Session, question_id: str) -> list[dict[str, Any]]:
    """All messages on a question's thread, oldest first."""

    stmt = (
        select(Message.id, Message.sender_employee_id, Message.body, Message.created_at)
        .where(Message.question_id == question_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return [
        {
            "id": mid,
            "sender_employee_id": sender_id,
            "body": body,
            "created_at": created_at.isoformat() if created_at is not None else None,
        }
        for mid, sender_id, body, created_at in session.execute(stmt)
    ]
