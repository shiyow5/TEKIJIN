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
dense matches on employee self-introductions, de-duplicated in order.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from tekijin.data.repository import Repository
from tekijin.retrieval import dense
from tekijin.retrieval.embedding import QUERY, Embedder
from tekijin.retrieval.fusion import rrf
from tekijin.retrieval.sparse import BM25Index


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

    def _fuse(self, dense_ids: list[Any], sparse_ids: list[Any]) -> list[tuple[Any, float]]:
        return rrf([dense_ids, sparse_ids], k=self._rrf_k)[: self._top_k]

    def _dense_ids(self, query_vec: list[float], target: str) -> list[Any]:
        return [id_ for id_, _ in dense.search(self._session, query_vec, target, self._top_k)]

    def search(self, query: str) -> dict[str, Any]:
        """Retrieve fused past answers, documents, and candidate people."""

        query_vec = self._embedder.encode([query], kind=QUERY)[0]

        answers = self._repo.list_answers()
        documents = self._repo.list_documents()
        responder_of = {a.id: a.responder_id for a in answers}

        answer_index = BM25Index.build((a.id, a.body or "") for a in answers)
        document_index = BM25Index.build(
            (d.id, f"{d.title or ''} {d.body or ''}") for d in documents
        )

        # --- past answers -------------------------------------------------- #
        sparse_answers = [id_ for id_, _ in answer_index.search(query, self._top_k)]
        fused_answers = self._fuse(self._dense_ids(query_vec, "answers"), sparse_answers)
        past_answers = [
            {"qa_id": id_, "score": score, "responder_id": responder_of.get(id_)}
            for id_, score in fused_answers
        ]

        # --- documents ----------------------------------------------------- #
        sparse_docs = [id_ for id_, _ in document_index.search(query, self._top_k)]
        fused_docs = self._fuse(self._dense_ids(query_vec, "documents"), sparse_docs)
        documents_out = [{"doc_id": id_, "score": score} for id_, score in fused_docs]

        # --- candidate people ---------------------------------------------- #
        dense_people = self._dense_ids(query_vec, "employee_profiles")
        candidate_people = self._aggregate_people(past_answers, dense_people)

        return {
            "past_answers": past_answers,
            "documents": documents_out,
            "candidate_people": candidate_people,
        }

    def _aggregate_people(
        self,
        past_answers: list[dict[str, Any]],
        dense_people: list[Any],
    ) -> list[Any]:
        """Order people by past-answer relevance first, then profile matches."""

        people: list[Any] = []
        for answer in past_answers:
            responder_id = answer["responder_id"]
            if responder_id is not None and responder_id not in people:
                people.append(responder_id)
        for employee_id in dense_people:
            if employee_id not in people:
                people.append(employee_id)
        return people[: self._top_k]
