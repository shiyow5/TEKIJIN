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

# All three thresholds live on the SAME RRF score scale the retriever emits
# (small sums of 1/(k+rank), ~0.01-0.05), so they are directly comparable — no
# cross-scale constant is ever compared against a score. Tuned on the eval set
# later.
#
# Minimum top past-answer score to route to ``prior_answer`` (strong prior QA).
PRIOR_ANSWER_THRESHOLD = 0.025
# Minimum top document score for the ``document`` demotion to be eligible.
DOCUMENT_THRESHOLD = 0.020
# At/above this top past-answer score the person signal is "strong enough" that a
# document must not demote it; below it the person signal is weak and a
# qualifying document may take over.
PERSON_STRONG_THRESHOLD = 0.015

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
    person_strong_threshold: float = PERSON_STRONG_THRESHOLD,
) -> RouteDecision:
    """Pick ``person`` / ``prior_answer`` / ``document`` from retrieval scores.

    Deterministic: depends only on the top scores and whether candidate people
    exist, never on iteration order. The default landing spot is always
    ``person``; the person signal is measured on the RRF scale by the best prior
    answer (``top_answer``), so ``document`` can genuinely win when the person
    signal is weak and a document out-scores it above its own bar.
    """

    past_answers = retrieval.get("past_answers") or []
    documents = retrieval.get("documents") or []
    candidate_people = retrieval.get("candidate_people") or []

    top_answer = _top_score(past_answers)
    top_document = _top_score(documents)

    if top_answer >= prior_answer_threshold:
        return RouteDecision(
            PRIOR_ANSWER,
            f"類似の過去回答が高スコア（{top_answer:.3f}）。回答者を主線として提示します。",
            top_answer,
        )
    # Demote to document only when the person signal is weak (no strong prior
    # answer) AND a document clears its bar AND out-scores that person signal.
    if (
        top_answer < person_strong_threshold
        and top_document >= document_threshold
        and top_document >= top_answer
    ):
        return RouteDecision(
            DOCUMENT,
            f"人の手がかりが弱く、社内文書が該当（{top_document:.3f}）。文書の場所を示します。",
            top_document,
        )
    if candidate_people:
        return RouteDecision(
            PERSON,
            "候補となる担当者が見つかりました。主線（人）で取り次ぎます。",
            top_answer,
        )
    return RouteDecision(
        PERSON,
        "確度の高い手がかりはありませんが、既定どおり人へ取り次ぎます。",
        0.0,
    )
