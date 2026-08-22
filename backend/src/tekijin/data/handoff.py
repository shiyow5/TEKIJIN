"""Read-only lookups for the responder-facing handoff view (GET /handoff).

Kept apart from the write side (:mod:`tekijin.data.writes`) and the dashboard
aggregations (:mod:`tekijin.data.dashboard`): these are the two DB reads the
handoff payload needs *beyond* the durable checkpoint state — the asking
employee's name/department, and the chosen responder's past-answer reuse totals
(the "見返り" number shown at the bottom of product-spec 画面4).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tekijin.models.tables import Answer, Employee


def employee_brief(session: Session, employee_id: int) -> tuple[str | None, str | None]:
    """Return ``(name, department)`` for an employee, or ``(None, None)`` if absent."""

    row = session.execute(
        select(Employee.name, Employee.department).where(Employee.id == employee_id)
    ).first()
    return (row[0], row[1]) if row else (None, None)


def responder_reuse_stats(session: Session, responder_id: int) -> dict[str, int]:
    """Aggregate a responder's past-answer reuse (the handoff's 見返り signal).

    ``reuse_count`` sums ``answers.reuse_count`` (NULLs coalesced to 0);
    ``helpful_answer_count`` counts answers explicitly marked ``was_helpful``.
    Both are 0 for a responder with no answers.
    """

    reuse_sum = session.scalar(
        select(func.coalesce(func.sum(Answer.reuse_count), 0)).where(
            Answer.responder_id == responder_id
        )
    )
    helpful = session.scalar(
        select(func.count())
        .select_from(Answer)
        .where(Answer.responder_id == responder_id, Answer.was_helpful.is_(True))
    )
    return {"reuse_count": int(reuse_sum or 0), "helpful_answer_count": int(helpful or 0)}
