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
    recent: int = 5,
) -> dict[str, Any]:
    """Aggregate counts, load distribution, topic mix, and recent recommendations."""

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

    # Most recent recommendations (empty until the recommendation loop persists).
    recent_stmt = (
        select(
            Recommendation.question_id,
            Recommendation.employee_id,
            Employee.name,
            Recommendation.score,
            Recommendation.outcome,
            Recommendation.created_at,
        )
        .join(Employee, Employee.id == Recommendation.employee_id)
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
        .limit(recent)
    )
    recent_recommendations = [
        {
            "question_id": qid,
            "employee_id": eid,
            "name": name,
            "score": score,
            "outcome": outcome,
            "created_at": created_at,
        }
        for qid, eid, name, score, outcome, created_at in session.execute(recent_stmt)
    ]

    return {
        "total_employees": total_employees,
        "total_questions": total_questions,
        "total_answers": total_answers,
        "recommendation_count": recommendation_count,
        "answers_per_responder": answers_per_responder,
        "topic_distribution": topic_distribution,
        "recent_recommendations": recent_recommendations,
    }
