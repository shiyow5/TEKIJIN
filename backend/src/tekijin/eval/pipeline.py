"""The real ranker: retrieval → route → deterministic expertise scoring.

Wires the production components (:class:`HybridRetriever`, :func:`decide_route`,
:class:`ExpertiseScorer`) into the :data:`~tekijin.eval.runner.Ranker` shape the
runner expects. Gold topics from the eval set are fed straight to the scorer, so
this measures the retrieval + ranking (layers 1–2) without depending on the C1
intent LLM — the route still comes from the retrieval confidences (C5).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

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

# RRF constant for rank-weighted topic voting (spec §7 default, == the retriever's
# own rrf_k). A retrieved answer at rank r contributes 1/(k+r+1) to each of its
# topics, so a topic backed by several high-ranked answers wins over a one-off hit.
_TOPIC_VOTE_K = 60
# Only the top handful of predicted topics matter (acc@1/acc@3 read the first 3).
_TOPIC_PREDICT_DEPTH = 10


def predict_topics_from_retrieval(
    retrieval: RetrievalResult,
    answer_topics: Mapping[str, list[str]],
    *,
    k: int = _TOPIC_VOTE_K,
    top_n: int = _TOPIC_PREDICT_DEPTH,
) -> list[str]:
    """Stage-A topic prediction from what retrieval surfaced (LLM-free, #71).

    The only topic-bearing retrieval channel is ``past_answers`` (documents carry
    no topic), so predicted topics are a rank-weighted vote over the topics of the
    top retrieved answers — mirroring ``research_topic.predict_topic_from_ranking``.
    ``answer_topics`` maps a qa_id to its topics (``answers.topic``, else the
    linked question's ``topics``). Returns topics best first; ties broken by name
    for determinism.
    """

    scores: dict[str, float] = {}
    for rank, answer in enumerate(retrieval.get("past_answers") or []):
        weight = 1.0 / (k + rank + 1)
        for topic in answer_topics.get(answer["qa_id"], ()):
            scores[topic] = scores.get(topic, 0.0) + weight
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [topic for topic, _ in ranked[:top_n]]


def build_answer_topics(repo: Repository) -> dict[str, list[str]]:
    """Map every answer id to the topics it evidences, for stage-A prediction.

    Prefers the answer's own ``topic``; falls back to its question's ``topics``
    array when the answer has none (runtime answers leave ``answers.topic`` NULL —
    the same fallback ``Repository.answers_by_topics`` relies on).
    """

    question_topics = {q.id: list(q.topics) for q in repo.list_questions()}
    out: dict[str, list[str]] = {}
    for answer in repo.list_answers():
        if answer.topic:
            out[answer.id] = [answer.topic]
        else:
            out[answer.id] = list(question_topics.get(answer.question_id, []))
    return out


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
        answer_topics: Mapping[str, list[str]] | None = None,
    ) -> None:
        self._retriever = retriever
        self._scorer = scorer
        self._now = now
        self._top_k = top_k
        # Empty by default so a fake-retriever unit test constructs without a DB;
        # then no answer maps to a topic and predicted_topics is simply empty.
        self._answer_topics = answer_topics or {}

    def __call__(self, query: EvalQuery) -> RankResult:
        retrieval = self._retriever.search(query.query)
        route = decide_route(retrieval).route
        return RankResult(
            ranked_experts=self._rank_experts(query, retrieval, route),
            route=route,
            predicted_topics=predict_topics_from_retrieval(retrieval, self._answer_topics),
        )

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

    repo = Repository(session)
    return PipelineRanker(
        # Widen the retrieval pool to match the rank depth, so a deeper top_k
        # actually surfaces more candidates (the retriever caps at its own top_k).
        retriever=HybridRetriever(embedder, session, top_k=top_k),
        scorer=ExpertiseScorer(repo),
        now=now,
        top_k=top_k,
        answer_topics=build_answer_topics(repo),
    )
