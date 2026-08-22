"""The real ranker: retrieval → route → deterministic expertise scoring.

Wires the production components (:class:`HybridRetriever`, :func:`decide_route`,
:class:`ExpertiseScorer`) into the :data:`~tekijin.eval.runner.Ranker` shape the
runner expects. Gold topics from the eval set are fed straight to the scorer, so
this measures the retrieval + ranking (layers 1–2) without depending on the C1
intent LLM — the route still comes from the retrieval confidences (C5).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from tekijin.agent.route import decide_route
from tekijin.agent.state import RetrievalResult
from tekijin.data.repository import Repository
from tekijin.eval.dataset import EvalQuery
from tekijin.eval.runner import RankResult
from tekijin.retrieval.embedding import Embedder
from tekijin.retrieval.retriever import HybridRetriever
from tekijin.scorer.scorer import ExpertiseScorer

# Rank deep enough that MRR reflects hits beyond the top 3 (Recall@3/Top-1 only
# read the first three, but a first-correct at rank 5 should still score 1/5).
_RANK_DEPTH = 10


def _pinned_responder(retrieval: RetrievalResult) -> int | None:
    """The responder of the highest-scoring past answer (prior_answer pin).

    Mirrors ``nodes.prior_answer`` / ``c6_score``: the past QA the router matched
    hands off to its responder. ``None`` when no past answer has a known responder.
    """

    past = retrieval.get("past_answers") or []
    ranked = sorted(past, key=lambda p: p.get("score", 0.0), reverse=True)
    for answer in ranked:
        responder = answer.get("responder_id")
        if responder is not None:
            return int(responder)
    return None


class PipelineRanker:
    """Rank experts for a query with the real retrieval + scorer pipeline."""

    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        scorer: ExpertiseScorer,
        now: dt.datetime,
        top_k: int = _RANK_DEPTH,
    ) -> None:
        self._retriever = retriever
        self._scorer = scorer
        self._now = now
        self._top_k = top_k

    def __call__(self, query: EvalQuery) -> RankResult:
        retrieval = self._retriever.search(query.query)
        route = decide_route(retrieval).route
        # Mirror the production graph: on the prior_answer route C6 scores ONLY the
        # pinned past responder (本人に取り次ぐ), not the whole pool — so Top-1 /
        # Recall / MRR reflect the expert the product actually presents.
        pinned = _pinned_responder(retrieval) if route == "prior_answer" else None
        candidates = [pinned] if pinned is not None else list(retrieval["candidate_people"])
        ranked = self._scorer.rank(
            query.gold_topics,
            candidates,
            None,  # asker unknown in offline eval — never filter anyone out
            self._now,
            top_k=self._top_k,
        )
        experts = [rec["person_id"] for rec in ranked["recommendations"]]
        return RankResult(ranked_experts=experts, route=route)


def build_pipeline_ranker(
    session: Session,
    embedder: Embedder,
    *,
    now: dt.datetime,
    top_k: int = _RANK_DEPTH,
) -> PipelineRanker:
    """Construct a :class:`PipelineRanker` bound to a DB session + embedder."""

    return PipelineRanker(
        # Widen the retrieval pool to match the rank depth, so a deeper top_k
        # actually surfaces more candidates (the retriever caps at its own top_k).
        retriever=HybridRetriever(embedder, session, top_k=top_k),
        scorer=ExpertiseScorer(Repository(session)),
        now=now,
        top_k=top_k,
    )
