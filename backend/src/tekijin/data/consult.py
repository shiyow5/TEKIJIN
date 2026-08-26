"""Durable reads for the 直接相談 retrospective (#247).

A face-to-face consultation is written up AFTER it happened, which is the one
thing the pending-hand-off view (``GET /handoff``) cannot serve: it is backed by
the checkpoint and disappears the moment the responder records an outcome. So the
retrospective reads SQL directly — ``questions`` (owner, body, consult method) +
``recommendations`` (who actually took it) + ``offline_consults`` (already
written?) — all of which outlive the run.

The accepted recommendation is also the AUTHORIZATION anchor for the write: it is
the only durable record of "this person was actually consulted", so it is what
decides whom a retrospective may be about. Without it, ``responder_id`` would be
free text and the row — which becomes expertise evidence — could name anyone.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import Employee, OfflineConsult, Question, Recommendation


def accepted_responder_id(session: Session, question_id: str) -> int | None:
    """The employee who ACCEPTED the hand-off for a question (None if nobody has).

    A decline+reroute leaves several rank-1 rows, but only one of them ever reaches
    ``outcome == "accepted"`` (see :class:`~tekijin.models.tables.Message`), so this
    is single-valued in practice; newest-first ordering keeps it deterministic if a
    future flow ever changes that.
    """

    return session.execute(
        select(Recommendation.employee_id)
        .where(
            Recommendation.question_id == question_id,
            Recommendation.outcome == "accepted",
        )
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
        .limit(1)
    ).scalar()


def has_retrospective(session: Session, question_id: str) -> bool:
    """True once a write-up exists for this question (the UI stops prompting)."""

    return (
        session.execute(
            select(OfflineConsult.id).where(OfflineConsult.question_id == question_id).limit(1)
        ).first()
        is not None
    )


def retrospective_context(session: Session, session_id: str) -> dict[str, Any] | None:
    """Everything the retrospective form needs, or ``None`` for an unknown session.

    Keyed by ``session_id`` because that is what the client holds after a run (the
    result screen's URL); ``questions.session_id`` is indexed for exactly this kind
    of lookup. ``consult_method`` is normalised at the API boundary, not here — the
    column is a bare VARCHAR with no CHECK constraint (#427).
    """

    row = session.execute(
        select(Question.id, Question.asker_id, Question.body, Question.consult_method)
        .where(Question.session_id == session_id)
        .order_by(Question.created_at.desc(), Question.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    question_id, asker_id, body, consult_method = row

    responder_id = accepted_responder_id(session, question_id)
    responder_name = (
        session.execute(select(Employee.name).where(Employee.id == responder_id)).scalar()
        if responder_id is not None
        else None
    )
    return {
        "question_id": question_id,
        "asker_id": asker_id,
        "question": body or "",
        "consult_method": consult_method,
        "responder_id": responder_id,
        "responder_name": responder_name,
        "already_recorded": has_retrospective(session, question_id),
    }
