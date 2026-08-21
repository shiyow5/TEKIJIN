"""C5: deterministic route decision (確信度 × 閾値).

Chooses how to resolve the question — no LLM, no randomness. Crucially it routes
on **absolute cosine similarity** (each channel's top dense ``1 - distance``,
supplied by C4 as ``*_confidence``), NOT on RRF fusion scores: an RRF score is a
sum of ``1/(k+rank)`` and has no absolute meaning across queries, so a fixed
threshold against it is meaningless. Cosine similarity is comparable, so the
thresholds below are real "how close is it?" gates. This also resolves the
dense-similarity floor deferred from #29.

Threshold rationale (cosine, good sentence embedding): a near-duplicate is
~0.85-0.95, strongly related ~0.75-0.85, topically related ~0.6-0.75, weak <0.5.
``PRIOR_ANSWER_SIM`` sits at 0.80 — the top of the "strongly related" band, i.e.
only a very close prior QA short-circuits; ``DOCUMENT_SIM`` at 0.70 is "strongly
related"; ``PERSON_WEAK_SIM`` at 0.50 is the "weak" ceiling.

Routes:
* ``prior_answer`` — a past QA is *very* close (near-duplicate): present who
  answered before, then hand off to that responder (flowchart PA → C6).
* ``document`` — the person signal is weak but a document is strongly on-topic:
  point at where it lives (答えは作らない).
* ``person`` — the main line and the default/fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tekijin.agent.state import Route

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tekijin.agent.state import RetrievalResult

# A past QA must be a near-duplicate to short-circuit to prior_answer.
PRIOR_ANSWER_SIM = 0.80
# A document must be strongly on-topic to be the demotion target.
DOCUMENT_SIM = 0.70
# Below this profile similarity the person signal counts as weak (a document may
# then take over). All three are cosine-similarity constants, tunable on eval.
PERSON_WEAK_SIM = 0.50

PERSON: Route = "person"
PRIOR_ANSWER: Route = "prior_answer"
DOCUMENT: Route = "document"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: Route
    reason: str
    confidence: float


def decide_route(
    retrieval: RetrievalResult,
    *,
    prior_answer_sim: float = PRIOR_ANSWER_SIM,
    document_sim: float = DOCUMENT_SIM,
    person_weak_sim: float = PERSON_WEAK_SIM,
) -> RouteDecision:
    """Pick ``person`` / ``prior_answer`` / ``document`` from channel confidences.

    Deterministic: depends only on the three absolute similarities and whether
    candidate people exist — never on iteration order. The default landing spot
    is always ``person``.
    """

    answer_conf = float(retrieval.get("answer_confidence", 0.0))
    document_conf = float(retrieval.get("document_confidence", 0.0))
    people_conf = float(retrieval.get("people_confidence", 0.0))
    candidate_people = retrieval.get("candidate_people") or []
    past_answers = retrieval.get("past_answers") or []

    # prior_answer needs BOTH a near-duplicate past QA AND actual past answers to
    # hand off to. answer_confidence already only counts questions that have
    # answers, so these agree; the past_answers check is an explicit safety gate.
    if answer_conf >= prior_answer_sim and past_answers:
        return RouteDecision(
            PRIOR_ANSWER,
            f"類似の過去QAが非常に近い（類似度 {answer_conf:.2f}）。回答者を主線として提示します。",
            answer_conf,
        )
    # Demote to document only when the person signal is weak (no near-duplicate
    # answer AND weak profile match) and a document is strongly on-topic. Reachable
    # even with candidate people present, when their profile match is weak. The
    # ``answer_conf < prior_answer_sim`` term is NOT redundant given the gate
    # above: we can reach here with a high answer_conf but empty past_answers, and
    # in that case a strong-answer query should stay on the person line, not demote.
    if (
        document_conf >= document_sim
        and people_conf < person_weak_sim
        and answer_conf < prior_answer_sim
    ):
        reason = f"社内文書が強く該当（類似度 {document_conf:.2f}）。文書の場所を示します。"
        return RouteDecision(DOCUMENT, reason, document_conf)
    if candidate_people:
        return RouteDecision(
            PERSON,
            "候補となる担当者が見つかりました。主線（人）で取り次ぎます。",
            max(people_conf, answer_conf),
        )
    return RouteDecision(
        PERSON,
        "確度の高い手がかりはありませんが、既定どおり人へ取り次ぎます。",
        0.0,
    )
