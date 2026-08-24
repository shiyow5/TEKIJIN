"""Feedback persistence + aggregation (#237 Phase 1).

The asking side's corrections of the AI's interpretation (C1), recommendation
(C6), or draft (C7) — the learning signal the runtime used to discard. Kept apart
from :mod:`tekijin.data.writes` only for cohesion; callers own the transaction
(``session_scope``), the same as the other write helpers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tekijin.models.tables import Feedback

# The pipeline stages a correction can target: intent (C1), recommendation (C6),
# draft (C7). Validated at the API boundary; kept here so the set has one home.
VALID_STAGES: tuple[str, ...] = ("c1", "c6", "c7")


def record_feedback(
    session: Session,
    *,
    stage: str,
    kind: str,
    question_id: str | None = None,
    session_id: str | None = None,
    target: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_id: int | None = None,
) -> None:
    """Insert one feedback row (see :class:`tekijin.models.tables.Feedback`)."""

    session.add(
        Feedback(
            stage=stage,
            kind=kind,
            question_id=question_id,
            session_id=session_id,
            target=target,
            payload=payload,
            actor_id=actor_id,
        )
    )


def feedback_counts_by_stage(session: Session) -> dict[str, int]:
    """Total feedback rows per stage (``{"c1": .., "c6": .., "c7": ..}``).

    Powers the dashboard's "どの段でどれだけずれているか" view. Stages with no
    feedback are omitted (the caller fills zeros), so the shape is stable.
    """

    rows = session.execute(select(Feedback.stage, func.count()).group_by(Feedback.stage)).all()
    return {stage: count for stage, count in rows}
