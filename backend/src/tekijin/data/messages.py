"""Read/write for accepted-recommendation chat threads (#224).

A "thread" is an accepted :class:`~tekijin.models.tables.Recommendation` row
(see :class:`~tekijin.models.tables.Message`'s docstring for why that id is a
stable, unique thread key). Read and write are kept in one module — unlike the
other ``data/`` modules split by read-only concern (``inbox``/``handoff``/
``history``) — because every operation here must first pass the same party
check (asker or the accepted responder), so read and write share that guard
rather than duplicating it across files.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from tekijin.models.tables import Employee, Message, Question, Recommendation


def thread_parties(session: Session, thread_id: int) -> dict[str, Any] | None:
    """Return the accepted thread's parties, or ``None`` if it doesn't exist or
    isn't accepted yet.

    Keys: ``question_id``, ``question_title``, ``asker_id``, ``asker_name``,
    ``asker_dept``, ``responder_id``, ``responder_name``, ``responder_dept``,
    ``accepted_at`` (``Question.resolved_at``, falling back to
    ``Recommendation.created_at`` when the former is unset).
    """

    Responder = aliased(Employee)
    row = session.execute(
        select(
            Question.id,
            Question.body,
            Question.asker_id,
            Employee.name,
            Employee.department,
            Recommendation.employee_id,
            Responder.name,
            Responder.department,
            Question.resolved_at,
            Recommendation.created_at,
        )
        .join(Question, Recommendation.question_id == Question.id)
        .join(Employee, Employee.id == Question.asker_id)
        .join(Responder, Responder.id == Recommendation.employee_id)
        .where(
            Recommendation.id == thread_id,
            Recommendation.outcome == "accepted",
            # A "direct" consultation never gets a chat thread — never even
            # existed for its own two parties. NULL (never chosen) falls back
            # to "chat", the implicit default.
            func.coalesce(Question.consult_method, "chat") != "direct",
        )
    ).first()
    if row is None:
        return None
    (
        question_id,
        question_body,
        asker_id,
        asker_name,
        asker_dept,
        responder_id,
        responder_name,
        responder_dept,
        resolved_at,
        rec_created_at,
    ) = row
    return {
        "question_id": question_id,
        "question_title": question_body or "",
        "asker_id": asker_id,
        "asker_name": asker_name,
        "asker_dept": asker_dept,
        "responder_id": responder_id,
        "responder_name": responder_name,
        "responder_dept": responder_dept,
        "accepted_at": resolved_at or rec_created_at,
    }


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def is_thread_party(session: Session, thread_id: int, employee_id: int) -> bool:
    """True if ``employee_id`` is the asker or the accepted responder of ``thread_id``."""

    parties = thread_parties(session, thread_id)
    if parties is None:
        return False
    return employee_id in (parties["asker_id"], parties["responder_id"])


def threads_for_employee(session: Session, employee_id: int) -> list[dict[str, Any]]:
    """Every accepted thread where ``employee_id`` is the asker or the responder,
    newest activity first (latest message time, else the accept time).

    Each item: ``thread_id``, ``question_id``, ``question_title``,
    ``counterpart_id``, ``counterpart_name``, ``counterpart_dept``,
    ``last_message``, ``last_message_at``, ``created_at`` (accept time).
    """

    last_message_sub = (
        select(
            Message.recommendation_id.label("recommendation_id"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .group_by(Message.recommendation_id)
        .subquery()
    )
    latest_message = aliased(Message)

    Responder = aliased(Employee)
    stmt = (
        select(
            Recommendation.id,
            Question.id,
            Question.body,
            Question.asker_id,
            Employee.name,
            Employee.department,
            Recommendation.employee_id,
            Responder.name,
            Responder.department,
            Question.resolved_at,
            Recommendation.created_at,
            latest_message.body,
            last_message_sub.c.last_message_at,
        )
        .join(Question, Recommendation.question_id == Question.id)
        .join(Employee, Employee.id == Question.asker_id)
        .join(Responder, Responder.id == Recommendation.employee_id)
        .outerjoin(last_message_sub, last_message_sub.c.recommendation_id == Recommendation.id)
        .outerjoin(
            latest_message,
            (latest_message.recommendation_id == Recommendation.id)
            & (latest_message.created_at == last_message_sub.c.last_message_at),
        )
        .where(
            Recommendation.outcome == "accepted",
            (Question.asker_id == employee_id) | (Recommendation.employee_id == employee_id),
            # "Direct" consultations never get a chat thread; NULL falls back
            # to "chat", the implicit default.
            func.coalesce(Question.consult_method, "chat") != "direct",
        )
        .order_by(
            func.coalesce(
                last_message_sub.c.last_message_at,
                func.coalesce(Question.resolved_at, Recommendation.created_at),
            ).desc()
        )
    )

    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for (
        thread_id,
        question_id,
        question_body,
        asker_id,
        asker_name,
        asker_dept,
        responder_id,
        responder_name,
        responder_dept,
        resolved_at,
        rec_created_at,
        last_message_body,
        last_message_at,
    ) in session.execute(stmt):
        # The latest-message join can return >1 row when two messages tie on
        # created_at; dedupe by thread_id (first row wins, order is irrelevant
        # among ties for the preview).
        if thread_id in seen:
            continue
        seen.add(thread_id)
        is_asker = employee_id == asker_id
        items.append(
            {
                "thread_id": thread_id,
                "question_id": question_id,
                "question_title": question_body or "",
                "counterpart_id": responder_id if is_asker else asker_id,
                "counterpart_name": responder_name if is_asker else asker_name,
                "counterpart_dept": responder_dept if is_asker else asker_dept,
                "last_message": last_message_body,
                "last_message_at": _iso(last_message_at),
                "created_at": _iso(resolved_at or rec_created_at),
            }
        )
    return items


def messages_for_thread(session: Session, thread_id: int) -> list[dict[str, Any]]:
    """Every message on ``thread_id``, oldest first.

    Caller must have already checked :func:`is_thread_party`.
    """

    rows = session.execute(
        select(Message.id, Message.sender_id, Message.body, Message.created_at)
        .where(Message.recommendation_id == thread_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return [
        {"id": mid, "sender_id": sender_id, "body": body, "created_at": _iso(created_at)}
        for mid, sender_id, body, created_at in rows
    ]


def create_message(
    session: Session, thread_id: int, sender_id: int, body: str, now: dt.datetime
) -> dict[str, Any]:
    """Insert one message. Caller must have already validated the thread is
    accepted and ``sender_id`` is a party (see :func:`is_thread_party`).
    """

    row = Message(recommendation_id=thread_id, sender_id=sender_id, body=body, created_at=now)
    session.add(row)
    session.flush()
    session.refresh(row)
    return {
        "id": row.id,
        "thread_id": row.recommendation_id,
        "sender_id": row.sender_id,
        "body": row.body,
        "created_at": _iso(row.created_at),
    }
