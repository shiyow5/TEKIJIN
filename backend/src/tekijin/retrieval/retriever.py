"""Hybrid retriever (component C4): Dense + BM25 fused with RRF.

Ties the pieces together: embed the query (C3), run dense pgvector search and
in-memory BM25 in parallel channels, and fuse each channel's ranked ids with
Reciprocal Rank Fusion. Produces the C4 output shape consumed by the route
selector (#30) and the agent (#31):

    {
      "past_answers":    [{"qa_id", "score", "responder_id"}, ...],
      "documents":       [{"doc_id", "score"}, ...],
      "candidate_people": [employee_id, ...],
    }

``candidate_people`` is seeded from the responders of the fused past answers
(the people who have actually answered similar questions), then extended with
people whose self-introduction matches — dense and sparse (BM25) profile hits
fused with RRF — de-duplicated in order.
"""

from __future__ import annotations

from itertools import zip_longest
from typing import Any

from sqlalchemy.orm import Session

from tekijin.data.repository import Repository
from tekijin.retrieval import dense
from tekijin.retrieval.embedding import QUERY, Embedder
from tekijin.retrieval.fusion import rrf
from tekijin.retrieval.sparse import BM25Index

# How many candidates each channel (dense / sparse) retrieves *before* fusion.
# RRF is applied to these pools and only then truncated to ``top_k``: cutting
# each channel to ``top_k`` first would discard items sitting just below the cut
# that RRF would otherwise lift above a single channel's top hits.
CANDIDATE_POOL = 50


class HybridRetriever:
    """Dense + sparse hybrid search over past answers, documents, and people.

    Args:
        embedder: Query embedder (C3). Injected so tests can use a fake.
        session: Active session for dense pgvector search and repository reads.
        top_k: Max hits per result section.
        rrf_k: RRF constant (spec default 60).
    """

    def __init__(
        self,
        embedder: Embedder,
        session: Session,
        *,
        top_k: int = 10,
        rrf_k: int = 60,
    ) -> None:
        if top_k <= 0:
            raise ValueError(f"top_k must be positive, got {top_k}")
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be positive, got {rrf_k}")
        self._embedder = embedder
        self._session = session
        self._repo = Repository(session)
        self._top_k = top_k
        self._rrf_k = rrf_k
        # Per-channel retrieval depth before fusion (see CANDIDATE_POOL).
        self._pool = max(top_k * 5, CANDIDATE_POOL)

    def _fuse(self, dense_ids: list[Any], sparse_ids: list[Any]) -> list[tuple[Any, float]]:
        # RRF over the full channel pools; truncate to top_k only afterwards.
        return rrf([dense_ids, sparse_ids], k=self._rrf_k)[: self._top_k]

    def _fused_ids(self, dense_ids: list[Any], sparse_ids: list[Any]) -> list[Any]:
        return [id_ for id_, _ in self._fuse(dense_ids, sparse_ids)]

    def _dense_ids(self, query_vec: list[float], target: str) -> list[Any]:
        return [id_ for id_, _ in dense.search(self._session, query_vec, target, self._pool)]

    def _dense_answer_ids(
        self,
        query_vec: list[float],
        answers_by_question: dict[str, list[str]],
    ) -> list[Any]:
        """Dense answer candidates from BOTH the answer and question vectors.

        A user often paraphrases the question rather than the answer, so a dense
        search over answer bodies alone (which are frequently generic procedures)
        can miss the right answer. Searching the ``questions`` target too and
        expanding each question hit to its answers recovers those cases. Direct
        answer hits come first; question-derived answers are appended, keeping
        the first occurrence of each id. RRF and the sparse channel re-rank.
        """

        merged = self._dense_ids(query_vec, "answers")
        seen = set(merged)
        for question_id in self._dense_ids(query_vec, "questions"):
            for answer_id in answers_by_question.get(question_id, ()):
                if answer_id not in seen:
                    seen.add(answer_id)
                    merged.append(answer_id)
        return merged

    @staticmethod
    def _answer_text(answer: Any, question_body_by_id: dict[str, str | None]) -> str:
        """BM25 index text for an answer: its question's body plus its own.

        Terms a user searches by (model numbers, jargon) often live only in the
        *question*, while the answer is a generic procedure. Indexing both keeps
        those answers reachable. Falls back to the answer body alone when the
        linked question has no body.
        """

        question_body = question_body_by_id.get(answer.question_id)
        answer_body = answer.body or ""
        if question_body:
            return f"{question_body} {answer_body}".strip()
        return answer_body

    def search(self, query: str) -> dict[str, Any]:
        """Retrieve fused past answers, documents, and candidate people."""

        query_vec = self._embedder.encode([query], kind=QUERY)[0]

        answers = self._repo.list_answers()
        questions = self._repo.list_questions()
        documents = self._repo.list_documents()
        profiles = self._repo.list_profiles()
        responder_of = {a.id: a.responder_id for a in answers}
        question_body_by_id = {q.id: q.body for q in questions}
        answers_by_question: dict[str, list[str]] = {}
        for a in answers:
            answers_by_question.setdefault(a.question_id, []).append(a.id)

        answer_index = BM25Index.build(
            (a.id, self._answer_text(a, question_body_by_id)) for a in answers
        )
        document_index = BM25Index.build(
            (d.id, f"{d.title or ''} {d.body or ''}") for d in documents
        )
        profile_index = BM25Index.build((p.employee_id, p.description or "") for p in profiles)

        # --- past answers -------------------------------------------------- #
        sparse_answers = [id_ for id_, _ in answer_index.search(query, self._pool)]
        dense_answers = self._dense_answer_ids(query_vec, answers_by_question)
        fused_answers = self._fuse(dense_answers, sparse_answers)
        past_answers = [
            {"qa_id": id_, "score": score, "responder_id": responder_of.get(id_)}
            for id_, score in fused_answers
        ]

        # --- documents ----------------------------------------------------- #
        sparse_docs = [id_ for id_, _ in document_index.search(query, self._pool)]
        fused_docs = self._fuse(self._dense_ids(query_vec, "documents"), sparse_docs)
        documents_out = [{"doc_id": id_, "score": score} for id_, score in fused_docs]

        # --- candidate people ---------------------------------------------- #
        dense_people = self._dense_ids(query_vec, "employee_profiles")
        sparse_people = [id_ for id_, _ in profile_index.search(query, self._pool)]
        fused_people = self._fused_ids(dense_people, sparse_people)
        candidate_people = self._aggregate_people(past_answers, fused_people)

        return {
            "past_answers": past_answers,
            "documents": documents_out,
            "candidate_people": candidate_people,
        }

    def _aggregate_people(
        self,
        past_answers: list[dict[str, Any]],
        profile_people: list[Any],
    ) -> list[Any]:
        """Merge responders and profile matches, then de-duplicate and cap.

        Responders (people who answered similar questions) are the stronger
        signal, but concatenating *all* of them ahead of profile matches lets a
        long responder list push every profile hit past ``top_k`` — so an exact
        profile match could vanish from the result. Instead the two ranked lists
        are round-robin interleaved, responder-first (responder, profile,
        responder, profile, …). Responders keep a slight edge (they lead and take
        the even slots) while a top profile match always lands at position 1,
        safely inside ``top_k``.
        """

        responders: list[Any] = []
        for answer in past_answers:
            responder_id = answer["responder_id"]
            if responder_id is not None and responder_id not in responders:
                responders.append(responder_id)

        people: list[Any] = []
        for responder_id, employee_id in zip_longest(responders, profile_people):
            for candidate in (responder_id, employee_id):
                if candidate is not None and candidate not in people:
                    people.append(candidate)
        return people[: self._top_k]
