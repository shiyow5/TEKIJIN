"""C5: deterministic route decision (確信度 × 閾値).

Chooses how to resolve the question from the C4 retrieval scores alone — no LLM,
no randomness. The default landing spot is always ``person`` (主線): handing the
question to a real expert is the product's guarantee, and every other route is
only taken when it clears its threshold. Thresholds are module constants tuned on
the eval set later; they are on the RRF score scale the retriever emits (small
sums of ``1/(k+rank)``), NOT the 0-1 similarity in the illustrative spec JSON.

Routes:
* ``prior_answer`` — a past answer scores highly: present who answered before,
  then still hand off to that responder (flowchart PA → C6).
* ``document`` — no strong person signal, but a document clears its bar: point at
  where it lives (答えは作らない).
* ``person`` — the main line and the fallback for everything below threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Minimum top past-answer RRF score to route to ``prior_answer``.
PRIOR_ANSWER_THRESHOLD = 0.025
# Minimum top document RRF score for the ``document`` demotion to be eligible.
DOCUMENT_THRESHOLD = 0.020
# Confidence credited to the person route just for having candidate people; a
# document only wins when it beats this (i.e. the person signal is weak).
PERSON_BASE_CONFIDENCE = 0.5

PERSON = "person"
PRIOR_ANSWER = "prior_answer"
DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: str
    reason: str
    confidence: float


def _top_score(items: list[dict[str, Any]], key: str = "score") -> float:
    return max((float(item.get(key, 0.0)) for item in items), default=0.0)


def decide_route(
    retrieval: dict[str, Any],
    *,
    prior_answer_threshold: float = PRIOR_ANSWER_THRESHOLD,
    document_threshold: float = DOCUMENT_THRESHOLD,
) -> RouteDecision:
    """Pick ``person`` / ``prior_answer`` / ``document`` from retrieval scores.

    Deterministic: depends only on the top scores and whether candidate people
    exist, never on iteration order.
    """

    past_answers = retrieval.get("past_answers") or []
    documents = retrieval.get("documents") or []
    candidate_people = retrieval.get("candidate_people") or []

    top_answer = _top_score(past_answers)
    top_document = _top_score(documents)
    # The person signal: strong when we have both candidates and a good prior
    # answer; a bare candidate list is still a moderate signal.
    person_confidence = max(top_answer, PERSON_BASE_CONFIDENCE) if candidate_people else 0.0

    if top_answer >= prior_answer_threshold:
        return RouteDecision(
            PRIOR_ANSWER,
            f"類似の過去回答が高スコア（{top_answer:.3f}）。回答者を主線として提示します。",
            top_answer,
        )
    if top_document >= document_threshold and top_document > person_confidence:
        return RouteDecision(
            DOCUMENT,
            f"人の手がかりが弱く、社内文書が該当（{top_document:.3f}）。文書の場所を示します。",
            top_document,
        )
    if candidate_people:
        return RouteDecision(
            PERSON,
            "候補となる担当者が見つかりました。主線（人）で取り次ぎます。",
            person_confidence,
        )
    return RouteDecision(
        PERSON,
        "確度の高い手がかりはありませんが、既定どおり人へ取り次ぎます。",
        0.0,
    )
