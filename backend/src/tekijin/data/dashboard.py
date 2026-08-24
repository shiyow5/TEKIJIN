"""Read-only dashboard aggregations (recommendation load, topic mix, activity).

Pure SQL over the seeded schema, returned as a plain dict for the API layer to
wrap in Pydantic. Every query has a deterministic ``ORDER BY`` (with a
tiebreaker) so the output is stable.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tekijin.data.feedback import VALID_STAGES, feedback_counts_by_stage
from tekijin.models.tables import Answer, Employee, EvalRun, Event, Question, Recommendation

# Routes that resolve a question WITHOUT contacting a live person — the numerator
# of the self-resolution rate (product-spec 画面5). Only ``document`` qualifies in
# the current graph: ``prior_answer`` pins the past responder and still runs
# through C6/C7 to the ``send`` human interrupt, so it is NOT self-resolved.
# (Counting a genuinely person-free prior_answer path would need runtime tracking
# of whether the asker accepted the past answer without asking again — a follow-up.)
_SELF_RESOLVED_ROUTES = ("document",)


def dashboard_summary(
    session: Session,
    *,
    top_responders: int = 5,
) -> dict[str, Any]:
    """Aggregate counts, load distribution, topic mix, and outcome ratios.

    Aggregate-only by design (product-spec §241-251): no individual records are
    enumerated — the dashboard summarises usage, it is not an audit log.
    """

    total_employees = session.scalar(select(func.count()).select_from(Employee)) or 0
    total_questions = session.scalar(select(func.count()).select_from(Question)) or 0
    total_answers = session.scalar(select(func.count()).select_from(Answer)) or 0
    recommendation_count = session.scalar(select(func.count()).select_from(Recommendation)) or 0

    # Load: who has answered the most (a proxy for workload distribution).
    load_stmt = (
        select(Answer.responder_id, Employee.name, func.count().label("c"))
        .join(Employee, Employee.id == Answer.responder_id)
        .group_by(Answer.responder_id, Employee.name)
        .order_by(func.count().desc(), Answer.responder_id)
        .limit(top_responders)
    )
    answers_per_responder = [
        {"employee_id": rid, "name": name, "answer_count": count}
        for rid, name, count in session.execute(load_stmt)
    ]

    # Topic distribution over the questions' topic arrays.
    topic_col = func.unnest(Question.topics).label("topic")
    topic_stmt = (
        select(topic_col, func.count().label("c"))
        .group_by(topic_col)
        .order_by(func.count().desc(), topic_col)
    )
    topic_distribution = [
        {"topic": topic, "count": count} for topic, count in session.execute(topic_stmt)
    ]

    # Outcome aggregation (accept/decline/pending) + acceptance ratio — the
    # "使うほど育つ" signal, as aggregates only (no per-record listing).
    outcome_stmt = select(Recommendation.outcome, func.count()).group_by(Recommendation.outcome)
    outcome_counts = {outcome: count for outcome, count in session.execute(outcome_stmt)}
    accepted = outcome_counts.get("accepted", 0)
    declined = outcome_counts.get("declined", 0)
    pending = outcome_counts.get(None, 0)
    decided = accepted + declined
    acceptance_rate = (accepted / decided) if decided else 0.0

    return {
        "total_employees": total_employees,
        "total_questions": total_questions,
        "total_answers": total_answers,
        "recommendation_count": recommendation_count,
        "recommendation_outcomes": {
            "accepted": accepted,
            "declined": declined,
            "pending": pending,
        },
        "acceptance_rate": acceptance_rate,
        "self_resolution_rate": _self_resolution_rate(session),
        "avg_resolution_hours": _avg_resolution_hours(session),
        "top_responder_share": _top_responder_share(answers_per_responder, total_answers),
        "processing_latency": _processing_latency(session),
        "latest_eval": _latest_eval(session),
        "answers_per_responder": answers_per_responder,
        "topic_distribution": topic_distribution,
        "feedback_by_stage": _feedback_by_stage(session),
    }


def _feedback_by_stage(session: Session) -> dict[str, int]:
    """Feedback counts per pipeline stage + total (#237 — どの段でどれだけずれているか).

    Every stage (c1/c6/c7) is present with an explicit 0 so the dashboard shape is
    stable before any feedback exists.
    """

    counts = feedback_counts_by_stage(session)
    by_stage = {stage: counts.get(stage, 0) for stage in VALID_STAGES}
    by_stage["total"] = sum(by_stage.values())
    return by_stage


def _self_resolution_rate(session: Session) -> float:
    """Share of routed questions resolved via an auxiliary route (画面5 自己解決率).

    Denominator is questions that carry a route (i.e. went through the system);
    numerator is those routed to ``prior_answer`` / ``document`` (no new live
    hand-off). 0.0 when nothing has been routed yet (pre-seeded questions have a
    NULL route).
    """

    routed = (
        session.scalar(select(func.count()).select_from(Question).where(Question.route.isnot(None)))
        or 0
    )
    if not routed:
        return 0.0
    self_resolved = (
        session.scalar(
            select(func.count())
            .select_from(Question)
            .where(Question.route.in_(_SELF_RESOLVED_ROUTES))
        )
        or 0
    )
    return self_resolved / routed


def _avg_resolution_hours(session: Session) -> float | None:
    """Mean hours from a question to its resolution (画面5 平均解決時間).

    Resolution time is the runtime ``questions.resolved_at`` when present (a live
    accept / self-resolution, #97), otherwise the earliest ``answers`` row (the
    seeded / historical Q&A). The earliest answer is used so a later follow-up
    answer does not inflate the time. ``None`` when nothing is resolved yet.
    """

    first_answer = (
        select(Answer.question_id.label("qid"), func.min(Answer.created_at).label("solved"))
        .group_by(Answer.question_id)
        .subquery()
    )
    # Runtime resolution wins; fall back to the first answer for seeded history.
    solved = func.coalesce(Question.resolved_at, first_answer.c.solved)
    hours = func.extract("epoch", solved - Question.created_at) / 3600.0
    return session.scalar(
        select(func.avg(hours))
        .select_from(Question)
        .outerjoin(first_answer, first_answer.c.qid == Question.id)
        .where(Question.created_at.isnot(None), solved.isnot(None))
    )


def _processing_latency(session: Session) -> dict[str, Any]:
    """p50/p95 of per-question AI processing time in ms (画面5 / #177).

    Sums each question's recorded stage durations (``events.ended_at -
    started_at``) — so human-wait gaps between run segments are excluded — then
    takes the median / 95th percentile across questions. Empty (no runs recorded
    yet) yields None percentiles with ``sample_size`` 0.
    """

    stage_ms = func.extract("epoch", Event.ended_at - Event.started_at) * 1000.0
    per_question = (
        select(func.sum(stage_ms).label("ms"))
        .where(Event.started_at.isnot(None), Event.ended_at.isnot(None))
        .group_by(Event.question_id)
        .subquery()
    )
    p50, p95, n = session.execute(
        select(
            func.percentile_cont(0.5).within_group(per_question.c.ms.asc()),
            func.percentile_cont(0.95).within_group(per_question.c.ms.asc()),
            func.count(),
        )
    ).one()
    return {
        "p50_ms": round(p50) if p50 is not None else None,
        "p95_ms": round(p95) if p95 is not None else None,
        "sample_size": n,
    }


def _top_responder_share(answers_per_responder: list[dict[str, Any]], total_answers: int) -> float:
    """Concentration on the single busiest responder (画面5 負荷分散).

    ``answers_per_responder`` is already ordered busiest-first, so its head is the
    max. 0.0 when there are no answers. NOTE: like 平均解決時間 this reads the
    ``answers`` distribution (seed / historical), so it does not yet move with
    live routing (API hand-offs write ``recommendations``, not ``answers``); and
    the spec's naive-order baseline comparison (product-spec §246) is an offline
    measure. Both are tracked as follow-ups (#97).
    """

    if not total_answers or not answers_per_responder:
        return 0.0
    return answers_per_responder[0]["answer_count"] / total_answers


def _latest_eval(session: Session) -> dict[str, Any] | None:
    """The most recent stored evaluation snapshot (画面5 推薦精度), or ``None``.

    ``None`` until ``python -m tekijin.eval`` has persisted a run — the dashboard
    then shows a clear "未計測" state rather than a fabricated number.
    """

    row = session.execute(
        select(EvalRun).order_by(EvalRun.created_at.desc(), EvalRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "top1_accuracy": row.top1_accuracy,
        "recall_at_3": row.recall_at_3,
        "mrr": row.mrr,
        "route_accuracy": row.route_accuracy,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
