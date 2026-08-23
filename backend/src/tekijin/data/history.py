"""Read-only lookup for an asker's own recent questions (GET /questions).

Powers the "最近あなたが解決した質問" panel on the question screen. Resolution is
derived from what the runtime actually records:

* an **accepted rank-1 recommendation** (the /answer accept path — the responder
  took it), or
* an **answer row** / a seeded ``status == 'answered'`` (fixture history, where
  the runtime never writes an ``answers`` row).

The responder shown is the accepting responder when there is one, else the first
answerer (seeded history). Deliberately scoped to the asker's OWN questions — it
is a personal recap, not a global feed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import Answer, Employee, Question, Recommendation


def recent_questions_for_asker(
    session: Session, asker_id: int, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Return the asker's most recent questions (newest first), with resolution.

    Each item: ``question_id``, ``title`` (body), ``resolved`` (bool),
    ``responder_name`` (str | None), ``created_at`` (ISO 8601 | None).
    """

    q_rows = session.execute(
        select(Question.id, Question.body, Question.status, Question.created_at)
        .where(Question.asker_id == asker_id)
        .order_by(Question.created_at.desc(), Question.id.desc())
        .limit(limit)
    ).all()
    if not q_rows:
        return []

    qids = [r[0] for r in q_rows]

    # Accepting responder (the /answer accept path): rank-1, outcome accepted.
    accepted: dict[str, str] = {
        qid: name
        for qid, name in session.execute(
            select(Recommendation.question_id, Employee.name)
            .join(Employee, Employee.id == Recommendation.employee_id)
            .where(
                Recommendation.question_id.in_(qids),
                Recommendation.rank == 1,
                Recommendation.outcome == "accepted",
            )
        ).all()
    }

    # First answerer (seeded history path), oldest answer wins.
    answered: dict[str, str] = {}
    for qid, name in session.execute(
        select(Answer.question_id, Employee.name)
        .join(Employee, Employee.id == Answer.responder_id)
        .where(Answer.question_id.in_(qids))
        .order_by(Answer.created_at.asc(), Answer.id.asc())
    ).all():
        answered.setdefault(qid, name)

    items: list[dict[str, Any]] = []
    for qid, body, status, created_at in q_rows:
        responder = accepted.get(qid) or answered.get(qid)
        resolved = qid in accepted or qid in answered or status == "answered"
        items.append(
            {
                "question_id": qid,
                "title": body or "",
                "resolved": resolved,
                "responder_name": responder,
                "created_at": created_at.isoformat() if created_at is not None else None,
            }
        )
    return items
