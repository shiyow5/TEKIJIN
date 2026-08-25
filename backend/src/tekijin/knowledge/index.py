"""Embed knowledge units into their pgvector column (#357 slice 3, index side).

The extraction pipeline (slice 2) leaves ``knowledge_units.embedding`` NULL — a
unit is structured text until it is indexed. This module encodes each unit's case
text (``problem`` + ``action`` + ``result``) with an :class:`Embedder` and writes
the vector back, the counterpart to :func:`search_knowledge_units` which reads it
at query time. Embedder-agnostic (dependency-injected) so tests use a deterministic
fake and ``scripts/embed_knowledge.py`` wires the real Nemotron encoder.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import KnowledgeUnit
from tekijin.retrieval.embedding import PASSAGE, Embedder


def unit_text(row: KnowledgeUnit) -> str:
    """The text indexed for a unit: problem → action → result (present parts only).

    A case is retrieved by its situation and its response, so all three fields form
    the passage. ``result`` is often NULL (未確定) and is simply omitted then.
    """

    parts = [row.problem or "", row.action or ""]
    if row.result:
        parts.append(row.result)
    return "\n".join(p for p in parts if p).strip()


def embed_knowledge_units(
    session: Session,
    embedder: Embedder,
    *,
    only_missing: bool = True,
    batch_size: int = 64,
) -> int:
    """Encode and persist embeddings for knowledge units; returns the count embedded.

    ``only_missing`` (default) skips units that already carry a vector so re-runs
    only fill gaps; pass ``False`` to re-embed all (e.g. after an extraction refresh
    changed the text). Units whose case text is empty are skipped. ``rejected`` units
    are NEVER embedded — a human discarded them, so spending encoder/DB writes on
    them is waste; ``unreviewed`` and ``approved`` are both embedded so that an
    approval after indexing needs no re-embed (:func:`search_knowledge_units` still
    gates on ``approved`` at query time). The caller owns the transaction; this
    flushes.
    """

    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")

    stmt = select(KnowledgeUnit).where(KnowledgeUnit.review_status != "rejected")
    if only_missing:
        stmt = stmt.where(KnowledgeUnit.embedding.is_(None))
    rows = list(session.scalars(stmt))

    pending = [(row, text) for row in rows if (text := unit_text(row))]
    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = embedder.encode([text for _, text in batch], kind=PASSAGE)
        for (row, _text), vector in zip(batch, vectors, strict=True):
            row.embedding = vector
            embedded += 1
    session.flush()
    return embedded
