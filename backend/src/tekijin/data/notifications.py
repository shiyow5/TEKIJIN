"""Read-only lookup for the asker's decline notifications (GET /notifications, #E7).

No generic notification table: a decline notification is derived directly from
``Recommendation`` decline events. A decline event is durably identified as a
``rank == 1``, ``outcome == 'declined'`` row — ``set_recommendation_outcome``
only ever writes the primary recommendation's outcome, and a reroute's next
candidate gets a FRESH ``rank == 1`` row (see ``agent/nodes.py::reroute`` /
``c6_score``), so this predicate selects each historical decline event exactly
once. "Seen" state lives on ``Recommendation.declined_seen_at`` (set by
``data.writes.ack_decline_notifications``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import Employee, Question, Recommendation


def pending_decline_notifications_for_asker(
    session: Session, asker_id: int
) -> list[dict[str, Any]]:
    """Declines the asker hasn't seen yet (newest first).

    Each item: ``id`` (the declined ``Recommendation`` row's id — also the ack
    target), ``question_id``, ``session_id`` (deep-link target, may be ``None``
    for pre-session-tracking rows), ``message`` (ready-to-render text),
    ``declined_person_name``, ``created_at`` (ISO 8601).
    """

    stmt = (
        select(
            Recommendation.id,
            Recommendation.created_at,
            Employee.name,
            Question.id,
            Question.session_id,
        )
        .join(Question, Recommendation.question_id == Question.id)
        .join(Employee, Employee.id == Recommendation.employee_id)
        .where(
            Question.asker_id == asker_id,
            Recommendation.rank == 1,
            Recommendation.outcome == "declined",
            Recommendation.declined_seen_at.is_(None),
        )
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
    )

    items: list[dict[str, Any]] = []
    for rec_id, created_at, declined_name, question_id, session_id in session.execute(stmt):
        items.append(
            {
                "id": rec_id,
                "question_id": question_id,
                "session_id": session_id,
                "message": f"{declined_name}さんに断られたので次の候補に依頼してください",
                "declined_person_name": declined_name,
                "created_at": created_at.isoformat() if created_at is not None else None,
            }
        )
    return items
