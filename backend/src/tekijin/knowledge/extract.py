"""Case-unit extraction pipeline (#357 slice 2).

Reads raw records (sales daily reports for the PoC), asks the LLM to distil each
into a *case* knowledge unit (``問題 → 打ち手 → 結果``) via forced structured
output, and upserts the result with its provenance. Deliberately an OFFLINE batch
(a script drives it), not a graph node — the graph never calls this, so it stays
inert until a later slice wires knowledge retrieval.

Design invariants (see the #357 RFC):
- **Provenance-faithful topics**: the unit's ``topics`` come from the source
  record's precomputed tags, never from the LLM, so the knowledge vocabulary can
  not drift from the eval gold's.
- **Non-cases are skipped**: a record the model marks ``extractable=false`` stores
  nothing — a status note is not a case.
- **Idempotent**: storage goes through :func:`upsert_knowledge_unit`, keyed on
  ``(source_type, source_id)``, so re-running refreshes rather than duplicates.

The extractor takes an injected ``model`` (a ``with_structured_output``-bound
runnable) so tests drive it with a deterministic fake and never touch a network.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.config import Settings, get_settings
from tekijin.data.knowledge import upsert_knowledge_unit
from tekijin.llm.schemas import CaseExtractionSchema
from tekijin.models.tables import DailyReport

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "あなたは営業日報から再利用可能な『ケース知識』を抽出するアナリストです。"
    "与えられた1件の記録だけを根拠に、顧客の課題(problem)・打ち手(action)・結果(result)を"
    "日本語で簡潔に切り出してください。厳守事項: "
    "(1) 記録に書かれていないことを創作しない。結果が書かれていなければ result は null。"
    "(2) 明確な課題と打ち手の両方が読み取れる場合のみ extractable=true。"
    "単なる状況メモ・課題や打ち手が無いものは extractable=false で構いません。"
    "(3) 業種(industry)は記録に明示があるときだけ入れる。"
    "(4) confidence は抽出の確からしさ(0.0-1.0)。"
)


@dataclass(frozen=True, slots=True)
class ExtractionSource:
    """One raw record queued for extraction (immutable input to the LLM).

    ``text`` is what the model reads; ``topics`` are the source record's
    precomputed tags that become the resulting unit's topics (provenance-faithful).
    """

    source_type: str
    source_id: str
    text: str
    topics: tuple[str, ...]


def daily_report_sources(
    session: Session, topic: str, *, limit: int | None = None
) -> list[ExtractionSource]:
    """Topic-bearing daily reports as extraction inputs, newest first.

    Selects reports whose precomputed ``topics`` array contains ``topic`` (the same
    ``= ANY(topics)`` filter the repository uses for questions). ``limit`` bounds a
    PoC run to a slice of the corpus. The report's ``content`` plus its ``issue``
    (if any) form the text the model reads.
    """

    stmt = (
        select(DailyReport)
        .where(DailyReport.topics.any(topic))  # type: ignore[arg-type]
        .order_by(DailyReport.report_date.desc(), DailyReport.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    sources: list[ExtractionSource] = []
    for row in session.scalars(stmt):
        text = row.content or ""
        if row.issue:
            text = f"{text}\n課題: {row.issue}"
        sources.append(
            ExtractionSource(
                source_type="daily_report",
                source_id=str(row.id),
                text=text,
                topics=tuple(row.topics or ()),
            )
        )
    return sources


class CaseExtractor:
    """Distils one :class:`ExtractionSource` into a case unit via structured output.

    Mirrors the vLLM C1/C2 adapters: an injected ``model`` (bound to
    :class:`CaseExtractionSchema`) is used when provided (tests), otherwise a real
    client is built from settings. :meth:`extract` returns ``None`` for a record the
    model declines (``extractable=false``), so the caller stores nothing; an empty
    tool call is raised instead of skipped (see :meth:`extract`).
    """

    def __init__(self, *, model: Any | None = None, settings: Settings | None = None) -> None:
        self._model = model
        self._settings = settings or get_settings()

    def _structured(self) -> Any:  # pragma: no cover - builds a network client
        from tekijin.llm.vllm import build_structured_model

        return build_structured_model(CaseExtractionSchema, self._settings)

    @staticmethod
    def prompt(source: ExtractionSource) -> list[tuple[str, str]]:
        return [("system", _SYSTEM_PROMPT), ("human", source.text)]

    def extract(self, source: ExtractionSource) -> CaseExtractionSchema | None:
        """Return the extracted case, or ``None`` if the record is not a case.

        An EMPTY structured output (``out is None`` — the LLM emitted no tool call)
        is a hard failure raised as ``ValueError``, matching every other adapter in
        ``llm/vllm.py``: it is the shape a refusal / prompt-injection tends to
        produce (#118) and must NOT be conflated with a legitimate "this record is
        not a case" (``extractable=false`` → ``None``). :func:`extract_and_store`
        isolates the raise per source so one bad response does not abort a batch.
        """

        model = self._model if self._model is not None else self._structured()
        out: CaseExtractionSchema | None = model.invoke(self.prompt(source))
        if out is None:
            raise ValueError(
                "case extraction: structured output was empty (no tool call from the LLM)"
            )
        if not out.extractable:
            return None
        return out


def extract_and_store(
    session: Session, sources: Sequence[ExtractionSource], extractor: CaseExtractor
) -> dict[str, int]:
    """Extract each source and upsert the cases; returns ``{seen, stored, skipped, errored}``.

    ``skipped`` counts records the model declined (not a case); ``errored`` counts
    records whose extraction or storage raised — those are ISOLATED per source
    (logged with the ``source_id`` and skipped) so one bad LLM response or a
    transient failure does not abort the whole batch and roll back the units already
    upserted this run. Storage is idempotent on provenance, so re-running over the
    same sources refreshes in place (and lets an earlier ``errored`` source succeed
    on a later run). The caller owns the transaction; this flushes once at the end.
    """

    counts = {"seen": 0, "stored": 0, "skipped": 0, "errored": 0}
    for source in sources:
        counts["seen"] += 1
        try:
            extraction = extractor.extract(source)
            if extraction is None:
                counts["skipped"] += 1
                continue
            upsert_knowledge_unit(
                session,
                kind="case",
                problem=extraction.problem,
                action=extraction.action,
                result=extraction.result,
                topics=source.topics,
                industry=extraction.industry,
                source_type=source.source_type,
                source_id=source.source_id,
                confidence=extraction.confidence,
            )
            counts["stored"] += 1
        except Exception:
            # Isolate the failure to this source: log which record and continue, so
            # a single malformed response / timeout does not discard the batch.
            counts["errored"] += 1
            logger.warning(
                "knowledge extraction failed for %s/%s",
                source.source_type,
                source.source_id,
                exc_info=True,
            )
    session.flush()
    return counts
