"""Read-only aggregates over the ``questions`` table (the "asking" side).

Separate from :mod:`tekijin.data.history` (which is one asker's OWN recap): this
counts ACROSS askers to answer "how many OTHER people have asked about this
area?" — the reassurance signal a new hire sees so an unfamiliar question does
not feel embarrassing or unique (#475 Screen 01, "あなただけではありません").
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from tekijin.models.tables import Question


def count_similar_prior_askers(
    session: Session,
    topics: Sequence[str],
    *,
    exclude_asker_id: int | None = None,
    exclude_question_id: str | None = None,
) -> int:
    """Count DISTINCT askers whose question shares at least one topic.

    Topic overlap (``questions.topics && :topics``, GIN-indexed via
    ``ix_questions_topics``) is used as a cheap "same area" proxy: the live
    question embedding is NOT persisted on the row (``persist_question`` leaves it
    NULL), so a dense-similarity count would read empty in production. The current
    asker and the current question are excluded so the number reads as "N OTHER
    people also asked about this", never counting the asker themselves. Empty /
    all-blank ``topics`` -> 0 (no reassurance rather than a misleading global count).
    """

    unique = [t for t in dict.fromkeys(topics) if t and t.strip()]
    if not unique:
        return 0
    stmt = select(func.count(distinct(Question.asker_id))).where(Question.topics.overlap(unique))
    if exclude_asker_id is not None:
        stmt = stmt.where(Question.asker_id != exclude_asker_id)
    if exclude_question_id is not None:
        stmt = stmt.where(Question.id != exclude_question_id)
    return int(session.execute(stmt).scalar_one() or 0)
