"""Read-only dashboard aggregations (recommendation load, topic mix, activity).

Pure SQL over the seeded schema, returned as a plain dict for the API layer to
wrap in Pydantic. Every query has a deterministic ``ORDER BY`` (with a
tiebreaker) so the output is stable.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tekijin.models.tables import Answer, Employee, Question, Recommendation


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
        "answers_per_responder": answers_per_responder,
        "topic_distribution": topic_distribution,
    }
