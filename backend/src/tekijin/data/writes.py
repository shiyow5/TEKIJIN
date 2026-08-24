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

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from tekijin.models.tables import Employee, EvalRun, Event, Message, Question, Recommendation

# One recorded run stage: (stage name, started_at, ended_at, meta).
EventRow = tuple[str, dt.datetime, dt.datetime, dict[str, Any] | None]


def employee_exists(session: Session, employee_id: int) -> bool:
    """True if ``employee_id`` is a real employee (asker FK pre-check).

    Called before inserting a Question so a bad ``asker_id`` becomes a clean 404
    at the boundary instead of an ``IntegrityError`` (FK violation) mid-flush.
    """

    return (
        session.execute(select(Employee.id).where(Employee.id == employee_id)).first() is not None
    )


def persist_question(
    session: Session,
    question_id: str,
    asker_id: int,
    body: str,
    now: dt.datetime,
    *,
    session_id: str | None = None,
) -> None:
    """Insert the asked question (``created_at`` from the run's injected ``now``).

    ``session_id`` is the graph thread_id; stored so the responder inbox (#123)
    can deep-link a pending handoff back to ``/answer/{session_id}``.
    """

    session.add(
        Question(
            id=question_id,
            asker_id=asker_id,
            body=body,
            topics=[],
            status="open",
            created_at=now,
            session_id=session_id,
        )
    )


def update_question_topics(session: Session, question_id: str, topics: list[str]) -> None:
    """Backfill C1's extracted topics onto the question (for the dashboard mix)."""

    session.execute(update(Question).where(Question.id == question_id).values(topics=list(topics)))


def update_question_route(session: Session, question_id: str, route: str) -> None:
    """Record the C5 route on the question (drives the dashboard 自己解決率)."""

    session.execute(update(Question).where(Question.id == question_id).values(route=route))


def record_events(session: Session, question_id: str, rows: list[EventRow]) -> None:
    """Persist per-stage run timing (technical-spec §7) — the latency KPI source.

    One ``events`` row per graph stage with its ``started_at``/``ended_at``; the
    dashboard sums these per question for p50/p95 processing latency (#177). Batch
    ``add_all`` in the caller's transaction; a no-op on an empty list. Human-wait
    gaps (followup/send interrupts) are excluded because each run *segment* records
    only its own stages, so the sum is compute time, not wall-clock.
    """

    if not rows:
        return
    session.add_all(
        Event(
            question_id=question_id,
            stage=stage,
            started_at=started_at,
            ended_at=ended_at,
            meta=meta,
        )
        for stage, started_at, ended_at, meta in rows
    )


def mark_question_resolved(session: Session, question_id: str, resolved_at: dt.datetime) -> None:
    """Stamp the runtime resolution time on the question, first-wins (#97).

    Only sets ``resolved_at`` when it is still NULL, so a decline→reroute→accept
    or a duplicate/replayed terminal cannot move an already-recorded resolution
    time. Drives the dashboard's average resolution time from live traffic.
    """

    session.execute(
        update(Question)
        .where(Question.id == question_id, Question.resolved_at.is_(None))
        .values(resolved_at=resolved_at)
    )


def insert_eval_run(session: Session, metrics: dict[str, Any]) -> int:
    """Persist an offline-evaluation snapshot; returns its row id.

    Stores only the aggregate metrics the dashboard shows — never per-query data.
    """

    row = EvalRun(
        top1_accuracy=metrics.get("top1_accuracy"),
        recall_at_3=metrics.get("recall_at_3"),
        mrr=metrics.get("mrr"),
        route_accuracy=metrics.get("route_accuracy"),
        n_ranked=metrics.get("n_ranked"),
        n_routed=metrics.get("n_routed"),
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return row.id


def insert_shown_recommendations(
    session: Session,
    question_id: str,
    recommendations: list[dict[str, Any]],
) -> list[int]:
    """Persist EVERY shown recommendation (rank 1..N) and return their ids in order.

    The list is already ranked (top first). ``rank`` is ``1``-based by position.
    The returned ids preserve that order, so ``ids[0]`` is the primary (top /
    handed-off) recommendation whose outcome the responder later records; the rest
    are stored as ``outcome=NULL`` "shown" rows for the dashboard / audit trail.

    ``created_at`` is intentionally NOT set here: the column's ``server_default``
    (``now()``) stamps the actual INSERT time, which is the recommendation's real
    generation moment. This is deliberately separate from the injected ``now`` the
    agent uses for scoring — a reroute inserts a fresh row whose ``created_at``
    reflects when it was produced, keeping the scorer's 7-day ``load`` window
    accurate (codex#4).
    """

    ids: list[int] = []
    for position, rec in enumerate(recommendations, start=1):
        row = Recommendation(
            question_id=question_id,
            employee_id=rec["person_id"],
            rank=position,
            score=rec.get("score"),
            reasons={"reasons": rec.get("reasons") or []},  # JSONB column typed as dict
            outcome=None,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        ids.append(row.id)
    return ids


def reorder_recommendation_ranks(session: Session, ordered_ids: list[int]) -> None:
    """Set ``rank`` (1-based) on these ``Recommendation`` rows to match ``ordered_ids``.

    Used when the asker reselects a different shown candidate as the hand-off
    target (#200/#A1): the durable checkpoint's ``recommendations`` order is the
    source of truth for the *graph*, but ``GET /inbox`` reads the DB ``rank``
    column directly (SQL-only, never consults the checkpoint) — so a reselect
    must also sync this projection, or the newly-selected responder's inbox will
    not show the pending handoff. ``ordered_ids`` are the specific row ids from
    the current checkpoint's ``recommendation_ids`` (not a question-wide query),
    so a decline+reroute's earlier rank==1 rows for the same question are never
    touched by this.
    """

    for position, rec_id in enumerate(ordered_ids, start=1):
        session.execute(
            update(Recommendation).where(Recommendation.id == rec_id).values(rank=position)
        )


def latest_primary_recommendation(session: Session, question_id: str) -> int | None:
    """The id of the most recent rank-1 recommendation for a question, if any.

    Durable fallback for outcome recording when the primary id was not written
    back into the checkpoint (e.g. a disconnect before ``update_state`` ran). On a
    decline+reroute there are several rank-1 rows; the newest (highest id) is the
    one currently being answered.
    """

    row = session.execute(
        select(Recommendation.id)
        .where(Recommendation.question_id == question_id, Recommendation.rank == 1)
        .order_by(Recommendation.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def recommendation_outcome(session: Session, recommendation_id: int) -> str | None:
    """The currently recorded outcome for a recommendation (``None`` if unset)."""

    return session.execute(
        select(Recommendation.outcome).where(Recommendation.id == recommendation_id)
    ).scalar_one_or_none()


def set_recommendation_outcome(session: Session, recommendation_id: int, outcome: str) -> None:
    """Record the responder's accept/decline on a recommendation, once.

    The update is guarded on ``outcome IS NULL`` so a duplicate submission — e.g. a
    lost acknowledgement retried after a process restart cleared the in-memory
    dedup guard — cannot overwrite an already-recorded outcome (idempotent write).
    """

    session.execute(
        update(Recommendation)
        .where(Recommendation.id == recommendation_id, Recommendation.outcome.is_(None))
        .values(outcome=outcome)
    )


def ack_decline_notifications(session: Session, asker_id: int, ids: list[int]) -> int:
    """Mark these decline notifications seen, scoped to ``asker_id``'s own questions (#E7).

    Scoping by ``asker_id`` (via a subquery on the owning question) means one
    asker can never acknowledge — and so hide — another asker's notification by
    guessing an id. Returns the number of rows actually updated.
    """

    if not ids:
        return 0
    result = session.execute(
        update(Recommendation)
        .where(
            Recommendation.id.in_(ids),
            Recommendation.declined_seen_at.is_(None),
            Recommendation.question_id.in_(
                select(Question.id).where(Question.asker_id == asker_id)
            ),
        )
        .values(declined_seen_at=func.now())
    )
    return result.rowcount or 0


class QuestionHasPendingHandoff(Exception):
    """The question has a live, unanswered hand-off — deletion is blocked (#207/#F8)."""


def soft_delete_question(session: Session, question_id: str, asker_id: int) -> bool:
    """Soft-delete a question owned by ``asker_id`` (#207/#F8).

    Sets ``deleted_at`` rather than removing the row: ``Recommendation`` /
    ``Answer`` / ``Event`` all hold a plain FK to ``questions.id`` with no
    cascade, and the dashboard's aggregates read historical rows regardless of
    deletion, so a hard delete would either FK-violate or silently distort
    those aggregates. Raises :class:`QuestionHasPendingHandoff` (mapped to 409
    at the API boundary) when a responder is currently being asked (a rank==1,
    outcome-IS-NULL recommendation row) — deleting the question out from under
    that live hand-off would orphan the responder's inbox item. Returns
    ``False`` (a clean no-op) if the question does not exist, is not owned by
    ``asker_id``, or was already deleted.
    """

    pending = session.execute(
        select(Recommendation.id).where(
            Recommendation.question_id == question_id,
            Recommendation.rank == 1,
            Recommendation.outcome.is_(None),
        )
    ).first()
    if pending is not None:
        raise QuestionHasPendingHandoff(question_id)

    result = session.execute(
        update(Question)
        .where(
            Question.id == question_id,
            Question.asker_id == asker_id,
            Question.deleted_at.is_(None),
        )
        .values(deleted_at=func.now())
    )
    return (result.rowcount or 0) > 0


def insert_message(
    session: Session, question_id: str, sender_employee_id: int, body: str
) -> Message:
    """Append one chat message to a question's post-acceptance thread (#E6)."""

    row = Message(question_id=question_id, sender_employee_id=sender_employee_id, body=body)
    session.add(row)
    session.flush()
    session.refresh(row)
    return row
