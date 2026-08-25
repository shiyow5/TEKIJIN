"""Read-only lookup for the company-wide knowledge list (GET /knowledge, #293/#301).

Surfaces recently ANSWERED questions (README "回答の出所は、常に人。") as reusable
knowledge: the original question, the answer itself, who answered it, their
department, its topics, and when. Deliberately NOT scoped to one asker — unlike
:func:`tekijin.data.history.recent_questions_for_asker`, the whole point is
"someone else already asked this," so every answered question is visible to
every authenticated user (#301's "「これに近い話、前にも誰かが聞いてたはず」").

Scoped to questions that have an ``answers`` row specifically (not merely an
accepted recommendation) — that is the only place actual answer TEXT lives, so
without it there is nothing to show as the "回答のまとめ" a card needs.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tekijin.data.dashboard import _self_resolution_rate, top_answerers
from tekijin.models.tables import Answer, Employee, Question

_ANSWER_ROW = (
    Question.id,
    Question.body,
    Question.topics,
    Question.session_id,
    Answer.body,
    Answer.created_at,
    Employee.name,
    Employee.department,
)


def list_knowledge(
    session: Session,
    *,
    q: str | None = None,
    department: str | None = None,
    topic: str | None = None,
    since: dt.date | None = None,
    until: dt.date | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """Recently-answered questions (newest answer first), paged, plus a summary.

    ``q`` is a case-insensitive substring match on the question body; ``topic``
    matches the question's topic array; ``department`` filters on the
    RESPONDER's department; ``since``/``until`` bound the ANSWER's timestamp
    (when it was actually given, not when the question was asked).

    A question with more than one ``answers`` row (rare — seed data is 1:1)
    contributes only its newest answer, deduped in Python after the query's
    ``ORDER BY``.

    Returns ``(items, total_matching, summary)``: ``total_matching`` is the
    count of ANSWERED questions matching the filters above, BEFORE the
    ``offset``/``limit`` page cut. ``summary`` reuses the dashboard's
    self-resolution rate and top-answerers aggregates (``top_answerers``) so no
    new aggregation logic is introduced for the side panel, and its
    ``total_items`` is the GLOBAL count of answered questions (independent of
    both the filters and the page), matching the DoD's "蓄積件数" site-wide stat.
    """

    global_total = session.scalar(select(func.count(func.distinct(Answer.question_id)))) or 0
    summary = {
        "total_items": global_total,
        "self_resolution_rate": _self_resolution_rate(session),
        "top_responders": top_answerers(session, limit=5),
    }

    stmt = (
        select(*_ANSWER_ROW)
        .select_from(Answer)
        .join(Question, Question.id == Answer.question_id)
        .join(Employee, Employee.id == Answer.responder_id)
        .order_by(Answer.created_at.desc(), Answer.id.desc())
    )
    if q:
        stmt = stmt.where(Question.body.ilike(f"%{q}%"))
    if department:
        stmt = stmt.where(Employee.department == department)
    if topic:
        stmt = stmt.where(Question.topics.any(topic))  # type: ignore[arg-type]
    if since:
        stmt = stmt.where(Answer.created_at >= since)
    if until:
        stmt = stmt.where(Answer.created_at <= until)

    matching: list[dict[str, Any]] = []
    seen: set[str] = set()
    for qid, q_body, topics, session_id, answer_body, answered_at, name, dept in session.execute(
        stmt
    ):
        if qid in seen:  # keep only the newest answer per question
            continue
        seen.add(qid)
        matching.append(
            {
                "question_id": qid,
                "title": q_body or "",
                "topics": list(topics or []),
                "answer_body": answer_body or "",
                "responder_name": name,
                "responder_department": dept,
                "resolved_at": answered_at.isoformat() if answered_at is not None else None,
                "session_id": session_id,
            }
        )
    return matching[offset : offset + limit], len(matching), summary
