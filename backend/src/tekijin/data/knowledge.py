"""Knowledge-unit persistence and reads (#357 — tacit → explicit).

The knowledge framework stores raw data as *structured, reusable* units
(:class:`tekijin.models.tables.KnowledgeUnit`) rather than as searchable free
text. This module is the CRUD skeleton for slice 1: create/upsert (idempotent on
provenance), review-status transitions, and topic-scoped reads that gate on
review. Extraction (LLM) and vector retrieval land in later slices; nothing here
runs unless a caller invokes it, and the graph does not yet.

Kept apart from :mod:`tekijin.data.repository` (read) / :mod:`tekijin.data.writes`
(write) purely for cohesion — a feature module like :mod:`tekijin.data.feedback`.
Callers own the transaction (``session_scope``).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tekijin.data.dto import KnowledgeUnitDTO
from tekijin.models.tables import KnowledgeUnit

# The knowledge-unit types. The PoC produces only ``case`` (problem → action →
# result); ``procedure`` / ``decision`` are reserved (see the table check).
VALID_KINDS: tuple[str, ...] = ("case", "procedure", "decision")

# Review lifecycle. A unit is ``unreviewed`` at extraction and only an
# ``approved`` unit is trusted by retrieval / self-answer; ``rejected`` marks a
# mis-extraction. The human-review導線 is #354.
VALID_REVIEW_STATUSES: tuple[str, ...] = ("unreviewed", "approved", "rejected")


def upsert_knowledge_unit(
    session: Session,
    *,
    kind: str,
    problem: str | None,
    action: str | None,
    result: str | None = None,
    topics: Sequence[str] | None = None,
    industry: str | None = None,
    source_type: str,
    source_id: str,
    confidence: float | None = None,
) -> None:
    """Insert or update one knowledge unit, keyed by its provenance.

    Idempotent on ``(source_type, source_id)``: re-extracting the same raw record
    updates the existing unit in place rather than creating a duplicate (one source
    record → at most one unit for the PoC). ``review_status`` is intentionally NOT
    touched on conflict — a human's ``approved`` / ``rejected`` decision survives a
    re-extraction, and the refreshed content simply awaits re-review if it matters.
    The embedding is likewise left as-is (recomputed by the ingestion slice).

    Validates ``kind`` at the boundary so a bad value is a clean ``ValueError``, not
    a mid-flush ``CheckViolation``. Provenance fields are required — a unit is never
    stored without a link back to the record it came from.
    """

    if kind not in VALID_KINDS:
        raise ValueError(f"unknown knowledge-unit kind: {kind!r}")
    if not source_type or not source_id:
        raise ValueError("knowledge unit requires (source_type, source_id) provenance")

    topic_list = list(topics or [])
    stmt = pg_insert(KnowledgeUnit).values(
        kind=kind,
        problem=problem,
        action=action,
        result=result,
        topics=topic_list,
        industry=industry,
        source_type=source_type,
        source_id=source_id,
        confidence=confidence,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_type", "source_id"],
        set_={
            "kind": stmt.excluded.kind,
            "problem": stmt.excluded.problem,
            "action": stmt.excluded.action,
            "result": stmt.excluded.result,
            "topics": stmt.excluded.topics,
            "industry": stmt.excluded.industry,
            "confidence": stmt.excluded.confidence,
        },
    )
    session.execute(stmt)


def set_review_status(session: Session, unit_id: int, status: str) -> None:
    """Record a human's review decision on one unit (``approved`` / ``rejected``).

    Validates ``status`` at the boundary. A non-existent id updates nothing
    (idempotent). Gating the retrieval path on ``approved`` is what keeps a
    mis-extracted unit out of answers.
    """

    if status not in VALID_REVIEW_STATUSES:
        raise ValueError(f"unknown review status: {status!r}")
    session.execute(
        update(KnowledgeUnit).where(KnowledgeUnit.id == unit_id).values(review_status=status)
    )


def get_knowledge_unit_by_source(
    session: Session, source_type: str, source_id: str
) -> KnowledgeUnitDTO | None:
    """The unit extracted from one raw record, or ``None`` (provenance lookup)."""

    row = session.execute(
        select(KnowledgeUnit).where(
            KnowledgeUnit.source_type == source_type,
            KnowledgeUnit.source_id == source_id,
        )
    ).scalar_one_or_none()
    return KnowledgeUnitDTO.from_row(row) if row is not None else None


def list_knowledge_units(
    session: Session, *, review_status: str | None = None
) -> list[KnowledgeUnitDTO]:
    """Every knowledge unit, ordered by id; optionally filtered by review status.

    ``review_status=None`` returns all (the review/management view, #354);
    ``'unreviewed'`` is the extraction queue awaiting a human. Unbounded by
    design at this slice (skeleton, no caller); the #354 management endpoint that
    wires this must add ``limit``/``offset`` pagination before exposing it.
    """

    stmt = select(KnowledgeUnit).order_by(KnowledgeUnit.id)
    if review_status is not None:
        if review_status not in VALID_REVIEW_STATUSES:
            raise ValueError(f"unknown review status: {review_status!r}")
        stmt = stmt.where(KnowledgeUnit.review_status == review_status)
    return [KnowledgeUnitDTO.from_row(r) for r in session.scalars(stmt)]


def knowledge_units_by_topics(
    session: Session,
    topics: Sequence[str],
    *,
    review_status: str | None = "approved",
) -> list[KnowledgeUnitDTO]:
    """Knowledge units whose ``topics`` overlap ``topics`` (the retrieval scope).

    Defaults to ``review_status='approved'`` so only human-trusted units reach a
    caller by default; pass ``None`` to include every status (e.g. an admin view).
    Ordered by id (deterministic). Empty ``topics`` → ``[]`` without a query.
    """

    topic_list = list(topics)
    if not topic_list:
        return []
    stmt = select(KnowledgeUnit).where(KnowledgeUnit.topics.overlap(topic_list))
    if review_status is not None:
        if review_status not in VALID_REVIEW_STATUSES:
            raise ValueError(f"unknown review status: {review_status!r}")
        stmt = stmt.where(KnowledgeUnit.review_status == review_status)
    stmt = stmt.order_by(KnowledgeUnit.id)
    return [KnowledgeUnitDTO.from_row(r) for r in session.scalars(stmt)]
