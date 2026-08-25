"""Answer a question directly from structured knowledge units (#357 slice 4).

The #327 measurement proved the self-answer path cannot be opened by smarter C5
routing (prior_answer is inseparable from person under every available signal —
ADR-0007). The route into self-answer is instead the **knowledge layer**: when an
approved case unit matches the query, compose the answer FROM its structure
(問題 → 打ち手 → 結果) and cite its provenance.

Crucially this composition is **deterministic — no LLM**. Because a knowledge unit
is already structured, the answer is BUILT from its fields, so it cannot
hallucinate: grounding is by construction, not by a model promising to stay
faithful. This sidesteps both the routing-separation problem (#327) and the
ungrounded-answer risk of an LLM composer (#291), and it is fast (#64).

Returns the same :class:`SelfAnswerResult` shape the #291 self-answer terminal
already consumes, so wiring this into the graph (a later slice) is a drop-in. The
service primitive returns ``None`` when no approved knowledge is relevant, so the
caller falls back to normal routing — never a degraded answer.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from tekijin.agent.protocols import SelfAnswerResult
from tekijin.data.dto import KnowledgeUnitDTO
from tekijin.data.knowledge import search_knowledge_units

# Citation id scheme for a knowledge unit: ``ku_{id}``. The existing #291 self-answer
# citations are the raw source id (a past-answer ``qa_id`` or a document id, no
# prefix — see retrieval/fragments.CitedEvidence), so the ``ku_`` prefix keeps a
# knowledge citation distinct from those raw ids and lets the UI / #354 management
# view resolve it back to the unit (and, through the unit, its source provenance).
KNOWLEDGE_CITATION_PREFIX = "ku_"


def knowledge_citation_id(unit: KnowledgeUnitDTO) -> str:
    return f"{KNOWLEDGE_CITATION_PREFIX}{unit.id}"


def _format_case(unit: KnowledgeUnitDTO) -> str:
    """Render one case unit as a compact 問題→打ち手→結果 block (present fields only)."""

    # Guard every optional field: problem/action are ``str | None`` on the DTO/column.
    # The extraction pipeline's validator guarantees them for a ``case`` unit, but a
    # manual write (#354) or a future procedure/decision kind could pass None — and a
    # bare f-string would leak the literal "None" into a user-facing answer. Consistent
    # with knowledge/index.unit_text, which is likewise defensive.
    lines = []
    if unit.industry:
        lines.append(f"【{unit.industry}】")
    if unit.problem:
        lines.append(f"課題: {unit.problem}")
    if unit.action:
        lines.append(f"打ち手: {unit.action}")
    if unit.result:
        lines.append(f"結果: {unit.result}")
    return "\n".join(lines)


def compose_knowledge_answer(
    units: Sequence[KnowledgeUnitDTO], *, top_n: int = 3
) -> SelfAnswerResult:
    """Deterministically compose a grounded answer from structured case units.

    No LLM: the answer is assembled from the top ``top_n`` units' fields, so it is
    grounded by construction and cites each unit (``ku_{id}``). Returns
    ``grounded=False`` with no citations when ``units`` is empty — the caller then
    falls back to routing rather than emitting an empty answer (matching the #291
    self-answer contract: a grounded result always carries at least one citation).
    """

    chosen = list(units)[:top_n]
    if not chosen:
        return SelfAnswerResult(answer="", cited_source_ids=[], grounded=False)

    intro = "社内の類似ケースからの回答です。"
    blocks = [_format_case(u) for u in chosen]
    answer = intro + "\n\n" + "\n\n".join(blocks)
    cited = [knowledge_citation_id(u) for u in chosen]
    return SelfAnswerResult(answer=answer, cited_source_ids=cited, grounded=True)


def answer_from_knowledge(
    session: Session,
    query_vec: Sequence[float],
    *,
    top_k: int = 5,
    top_n: int = 3,
    min_similarity: float = 0.0,
) -> SelfAnswerResult | None:
    """Search approved knowledge and compose an answer, or ``None`` if none is relevant.

    Retrieves the top ``top_k`` approved units by cosine similarity, keeps those at
    or above ``min_similarity`` (the relevance floor — calibrated on the eval, see
    the #357 slice-4b A/B), and composes from the best ``top_n``. ``None`` means "no
    approved knowledge answers this" so the caller routes normally; this never
    returns an ungrounded answer. Pure (no config/flag read) — the ``knowledge_
    retrieval_enabled`` gate lives at the call site.

    NOTE for the slice-4c wiring: ``min_similarity`` defaults to ``0.0`` (no floor)
    to keep this pure; the live caller MUST pass ``settings.knowledge_answer_min_
    similarity`` explicitly, or every retrieved unit would qualify.
    """

    hits = search_knowledge_units(session, query_vec, top_k=top_k, review_status="approved")
    relevant = [unit for unit, sim in hits if sim >= min_similarity]
    if not relevant:
        return None
    return compose_knowledge_answer(relevant, top_n=top_n)
