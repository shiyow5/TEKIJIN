"""Read-only lookup for a single internal document (GET /documents/{doc_id}).

Powers the document viewer (#143): when a question is answered by the ``document``
route, the client receives the cited ``doc_id`` on the terminal ``message`` event
and fetches the full content here to show it. Deliberately read-only — viewing a
document never advances any run or writes anything.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import Document


def get_document(session: Session, doc_id: str) -> dict[str, Any] | None:
    """Return one document's full content, or ``None`` if the id is unknown.

    Item: ``id``, ``title``, ``body``, ``source``, ``updated_at`` (ISO 8601 | None).
    """

    row = session.execute(
        select(
            Document.id,
            Document.title,
            Document.body,
            Document.source,
            Document.updated_at,
        ).where(Document.id == doc_id)
    ).first()
    if row is None:
        return None
    doc_id_, title, body, source, updated_at = row
    return {
        "id": doc_id_,
        "title": title,
        "body": body,
        "source": source,
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
    }
