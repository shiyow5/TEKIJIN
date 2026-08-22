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
    """The responder of THE single highest-scoring past answer (prior_answer pin).

    Mirrors ``nodes.prior_answer`` exactly: it pins the responder of the one
    top-scored past QA. If that top answer has no known responder, the pin is
    ``None`` and ``c6_score`` falls back to the full candidate pool — so we do NOT
    fall through to a lower-scored answer here.
    """

    past = retrieval.get("past_answers") or []
    if not past:
        return None
    top = max(past, key=lambda p: p.get("score", 0.0))
    responder = top.get("responder_id")
    return int(responder) if responder is not None else None


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
        return RankResult(ranked_experts=self._rank_experts(query, retrieval, route), route=route)

    def _rank_experts(self, query: EvalQuery, retrieval: RetrievalResult, route: str) -> list[int]:
        """Mirror the production graph so the metrics reflect what the product shows.

        * ``document`` is a terminal route — C6 never runs, so no experts are
          presented (only the document location).
        * ``prior_answer`` pins the top past responder and scores ONLY them.
        * ``c6_score`` returns nothing when there are no topics or no candidates —
          so an empty ``gold_topics`` (unsupported-topic rows) yields no ranking
          rather than an arbitrary load/id tie-break the product would never emit.
        """

        if route == "document":
            return []
        pinned = _pinned_responder(retrieval) if route == "prior_answer" else None
        candidates = [pinned] if pinned is not None else list(retrieval["candidate_people"])
        if not query.gold_topics or not candidates:
            return []
        ranked = self._scorer.rank(
            query.gold_topics,
            candidates,
            None,  # asker unknown in offline eval — never filter anyone out
            self._now,
            top_k=self._top_k,
        )
        return [rec["person_id"] for rec in ranked["recommendations"]]


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
