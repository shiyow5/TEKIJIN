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

``candidate_people`` is built by round-robin interleaving (responder-first) the
responders of the fused past answers with the profile-match ranking — dense and
sparse (BM25) self-introduction hits fused with RRF — then de-duplicated. The
interleave (rather than "all responders, then profiles") keeps an exact profile
match from being crowded out of ``top_k`` by many answer responders; see
:meth:`_aggregate_people`.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import zip_longest
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from tekijin.data.repository import Repository
from tekijin.retrieval import dense
from tekijin.retrieval.embedding import QUERY, Embedder
from tekijin.retrieval.fusion import rrf
from tekijin.retrieval.sparse import BM25Index

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids agent<-retrieval cycle)
    from tekijin.agent.state import DocumentHit, PastAnswer, RetrievalResult

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

    def _fuse(self, *rankings: list[Any]) -> list[tuple[Any, float]]:
        # RRF over the full channel pools; truncate to top_k only afterwards.
        return rrf(list(rankings), k=self._rrf_k)[: self._top_k]

    def _fused_ids(self, *rankings: list[Any]) -> list[Any]:
        return [id_ for id_, _ in self._fuse(*rankings)]

    def _dense_hits(self, query_vec: Sequence[float], target: str) -> list[tuple[Any, float]]:
        """``(id, cosine_similarity)`` for ``target``, best-first."""

        return dense.search(self._session, query_vec, target, self._pool)

    @staticmethod
    def _top_similarity(*hit_lists: list[tuple[Any, float]]) -> float:
        """Highest cosine similarity across the given dense hit lists (``0.0`` if
        all empty). This is the channel's absolute confidence for C5."""

        return max((sim for hits in hit_lists for _, sim in hits), default=0.0)

    @staticmethod
    def _question_mapped_answer_ids(
        question_hits: list[tuple[Any, float]],
        answers_by_question: dict[str, list[str]],
    ) -> list[Any]:
        """Answer ids ranked by DENSE similarity of their *question*.

        A user often paraphrases the question rather than the answer, so a dense
        search over answer bodies alone (which are frequently generic procedures)
        can miss the right answer. This expands each question hit to its answers,
        preserving the question ranking (first occurrence wins on duplicates). It
        is fed to RRF as an independent ranking — NOT appended behind the
        direct-answer pool — so an answer whose question matches strongly ranks
        near the top of its own list and gains a large RRF contribution.
        """

        ranked: list[Any] = []
        seen: set[Any] = set()
        for question_id, _sim in question_hits:
            for answer_id in answers_by_question.get(question_id, ()):
                if answer_id not in seen:
                    seen.add(answer_id)
                    ranked.append(answer_id)
        return ranked

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

    def search(self, query: str, *, query_vector: Sequence[float] | None = None) -> RetrievalResult:
        """Retrieve fused past answers, documents, and candidate people.

        ``query_vector`` is the C3 embedding of ``query``; pass it to avoid a
        second embedding call (the dense channels reuse it, BM25 uses the raw
        ``query``). When omitted, the query is embedded here.
        """

        query_vec = (
            query_vector
            if query_vector is not None
            else self._embedder.encode([query], kind=QUERY)[0]
        )

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

        # Dense searches (each returns (id, cosine_similarity), best-first).
        answer_hits = self._dense_hits(query_vec, "answers")
        question_hits = self._dense_hits(query_vec, "questions")
        document_hits = self._dense_hits(query_vec, "documents")
        people_hits = self._dense_hits(query_vec, "employee_profiles")

        # Absolute per-channel confidence for C5 (top cosine similarity). The
        # answer channel is the closest of a matching past *answer* or *question*.
        answer_confidence = self._top_similarity(answer_hits, question_hits)
        document_confidence = self._top_similarity(document_hits)
        people_confidence = self._top_similarity(people_hits)

        # --- past answers -------------------------------------------------- #
        # Three independent rankings fused by RRF: dense over answer bodies, dense
        # over question bodies (mapped to their answers), and sparse BM25.
        sparse_answers = [id_ for id_, _ in answer_index.search(query, self._pool)]
        dense_answers = [id_ for id_, _ in answer_hits]
        question_answers = self._question_mapped_answer_ids(question_hits, answers_by_question)
        fused_answers = self._fuse(dense_answers, question_answers, sparse_answers)
        past_answers: list[PastAnswer] = [
            {"qa_id": id_, "score": score, "responder_id": responder_of.get(id_)}
            for id_, score in fused_answers
        ]

        # --- documents ----------------------------------------------------- #
        sparse_docs = [id_ for id_, _ in document_index.search(query, self._pool)]
        fused_docs = self._fuse([id_ for id_, _ in document_hits], sparse_docs)
        documents_out: list[DocumentHit] = [
            {"doc_id": id_, "score": score} for id_, score in fused_docs
        ]

        # --- candidate people ---------------------------------------------- #
        dense_people = [id_ for id_, _ in people_hits]
        sparse_people = [id_ for id_, _ in profile_index.search(query, self._pool)]
        fused_people = self._fused_ids(dense_people, sparse_people)
        candidate_people = self._aggregate_people(past_answers, fused_people)

        return {
            "past_answers": past_answers,
            "documents": documents_out,
            "candidate_people": candidate_people,
            "answer_confidence": answer_confidence,
            "document_confidence": document_confidence,
            "people_confidence": people_confidence,
        }

    def _aggregate_people(
        self,
        past_answers: Sequence[PastAnswer],
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
