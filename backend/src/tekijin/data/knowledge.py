"""Read-only lookup for the company-wide knowledge list (GET /knowledge, #293/#301).

Surfaces questions a PERSON actually resolved (README "回答の出所は、常に人。") as
reusable knowledge: the original question, who answered it, their department, its
topics, and when. Deliberately NOT scoped to one asker — unlike
:func:`tekijin.data.history.recent_questions_for_asker`, the whole point is
"someone else already asked this," so every resolved question is visible to every
authenticated user (#301's "「これに近い話、前にも誰かが聞いてたはず」").
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tekijin.data.dashboard import _self_resolution_rate, top_answerers
from tekijin.models.tables import Answer, Employee, Question, Recommendation

# question_id -> (responder_name, responder_department)
_ResponderMap = dict[str, tuple[str, str | None]]


def _person_resolved_responders(session: Session) -> tuple[_ResponderMap, _ResponderMap]:
    """(accepted, answered) responder maps, company-wide.

    Mirrors ``history.py``'s precedence — an accepted rank-1 recommendation (the
    live /answer accept path) wins over a seeded ``answers`` row — but is not
    filtered to one asker.
    """

    accepted: _ResponderMap = {}
    for qid, name, dept in session.execute(
        select(Recommendation.question_id, Employee.name, Employee.department)
        .join(Employee, Employee.id == Recommendation.employee_id)
        .where(Recommendation.rank == 1, Recommendation.outcome == "accepted")
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
    ).all():
        accepted.setdefault(qid, (name, dept))

    answered: _ResponderMap = {}
    for qid, name, dept in session.execute(
        select(Answer.question_id, Employee.name, Employee.department)
        .join(Employee, Employee.id == Answer.responder_id)
        .order_by(Answer.created_at.asc(), Answer.id.asc())
    ).all():
        answered.setdefault(qid, (name, dept))

    return accepted, answered


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
    """Resolved-by-a-person questions (newest first), paged, plus a summary.

    ``q`` is a case-insensitive substring match on the question body; ``topic``
    matches the question's topic array; ``since``/``until`` bound the resolution
    date (``resolved_at``, falling back to ``created_at`` for seeded history).
    ``department`` filters on the RESPONDER's department (applied in Python — it
    depends on the accepted/answered lookup, not a column on ``questions``), so
    ``offset``/``limit`` paging is also applied in Python, AFTER that filter (a
    SQL OFFSET/LIMIT here could skip past rows the department filter would have
    dropped anyway).

    Returns ``(items, total_matching, summary)``: ``total_matching`` is the
    count of questions matching ``q``/``department``/``topic``/``since``/
    ``until`` BEFORE the ``offset``/``limit`` page cut — what the frontend needs
    to render page controls for a search. ``summary`` reuses the dashboard's
    self-resolution rate and top-answerers aggregates (``top_answerers``) so no
    new aggregation logic is introduced for the side panel, and its
    ``total_items`` is the GLOBAL count (independent of both the filters and the
    page), matching the DoD's "蓄積件数" site-wide stat.
    """

    accepted, answered = _person_resolved_responders(session)
    resolved_qids = set(accepted) | set(answered)
    summary = {
        "total_items": len(resolved_qids),
        "self_resolution_rate": _self_resolution_rate(session),
        "top_responders": top_answerers(session, limit=5),
    }
    if not resolved_qids:
        return [], 0, summary

    stmt = select(
        Question.id,
        Question.body,
        Question.topics,
        Question.resolved_at,
        Question.created_at,
        Question.session_id,
    ).where(Question.id.in_(resolved_qids))
    if q:
        stmt = stmt.where(Question.body.ilike(f"%{q}%"))
    if topic:
        stmt = stmt.where(Question.topics.any(topic))  # type: ignore[arg-type]
    when_col = func.coalesce(Question.resolved_at, Question.created_at)
    if since:
        stmt = stmt.where(when_col >= since)
    if until:
        stmt = stmt.where(when_col <= until)
    stmt = stmt.order_by(when_col.desc(), Question.id.desc())

    matching: list[dict[str, Any]] = []
    for qid, body, topics, resolved_at, created_at, session_id in session.execute(stmt):
        name, dept = accepted.get(qid) or answered.get(qid)
        if department and dept != department:
            continue
        when = resolved_at or created_at
        matching.append(
            {
                "question_id": qid,
                "title": body or "",
                "topics": list(topics or []),
                "responder_name": name,
                "responder_department": dept,
                "resolved_at": when.isoformat() if when is not None else None,
                "session_id": session_id,
            }
        )
    return matching[offset : offset + limit], len(matching), summary
