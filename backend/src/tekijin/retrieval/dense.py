"""Dense (vector) retrieval over pgvector.

Runs an approximate-nearest-neighbour search against the ``embedding`` columns
using pgvector's cosine distance operator. Rows whose embedding is ``NULL`` (not
yet indexed) are excluded. Distance is converted to a cosine *similarity*
(``1 - distance``) so higher is better, matching the BM25 and RRF conventions.

Only the ordering feeds RRF downstream; the similarity is returned for
diagnostics and thresholding.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import Answer, Document, EmployeeProfile, Question

# Retrieval target -> (ORM model, primary-key attribute exposed as the hit id).
_TARGETS: dict[str, tuple[type, str]] = {
    "answers": (Answer, "id"),
    "documents": (Document, "id"),
    "questions": (Question, "id"),
    "employee_profiles": (EmployeeProfile, "employee_id"),
}

TARGETS: tuple[str, ...] = tuple(_TARGETS)


def search(
    session: Session,
    query_vec: Sequence[float],
    target: str,
    top_k: int = 10,
) -> list[tuple[Any, float]]:
    """Nearest neighbours of ``query_vec`` among ``target`` rows.

    Args:
        session: Active SQLAlchemy session.
        query_vec: Query embedding; its length must match the column width
            (``settings.embedding_dim``).
        target: One of :data:`TARGETS`.
        top_k: Maximum number of hits.

    Returns:
        ``(id, similarity)`` pairs ordered by descending cosine similarity.
    """

    try:
        model, id_attr = _TARGETS[target]
    except KeyError:
        raise ValueError(f"unknown target {target!r}; expected one of {TARGETS}") from None

    distance = model.embedding.cosine_distance(query_vec).label("distance")
    stmt = (
        select(model, distance).where(model.embedding.isnot(None)).order_by(distance).limit(top_k)
    )
    rows = session.execute(stmt).all()
    return [(getattr(obj, id_attr), 1.0 - float(dist)) for obj, dist in rows]
