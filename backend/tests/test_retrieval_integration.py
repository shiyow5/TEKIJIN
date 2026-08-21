"""Integration tests for the retrieval layer against live PostgreSQL + pgvector.

Uses the shared ``engine`` / ``seed_counts`` / ``session`` fixtures (conftest):
CI's pgvector service via ``TEKIJIN_DATABASE_URL`` or an ephemeral ``pgserver``
locally, skipped when neither is available. Dense vectors are injected with the
deterministic ``fake_embedder`` — no model download.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tekijin.data.repository import Repository
from tekijin.models.tables import Answer, Document, EmployeeProfile, Question
from tekijin.retrieval import dense
from tekijin.retrieval.indexing import embed_corpus
from tekijin.retrieval.retriever import HybridRetriever


def _vec(fake_embedder, text: str) -> list[float]:
    """Deterministic embedding for a single text via the injected fake."""

    return fake_embedder.encode([text])[0]


# --------------------------------------------------------------------------- #
# repository extension
# --------------------------------------------------------------------------- #
def test_list_answers(seed_counts, session) -> None:
    answers = Repository(session).list_answers()
    assert len(answers) == 150
    assert all(a.responder_id is not None for a in answers)


# --------------------------------------------------------------------------- #
# dense search
# --------------------------------------------------------------------------- #
def test_dense_search_ranks_nearest_first(seed_counts, session, fake_embedder) -> None:
    a, b, c = session.scalars(select(Answer).order_by(Answer.id).limit(3)).all()
    a.embedding = _vec(fake_embedder, "alpha unique token")
    b.embedding = _vec(fake_embedder, "beta different token")
    c.embedding = _vec(fake_embedder, "gamma other token")
    session.flush()

    query_vec = _vec(fake_embedder, "alpha unique token")
    hits = dense.search(session, query_vec, "answers", top_k=5)

    # Only the three embedded rows are eligible; NULL-embedding rows are excluded.
    assert len(hits) == 3
    assert hits[0][0] == a.id
    assert hits[0][1] == pytest.approx(1.0, abs=1e-6)  # identical vector -> cosine 1
    # Descending similarity.
    sims = [s for _, s in hits]
    assert sims == sorted(sims, reverse=True)


def test_dense_search_excludes_null_embeddings(seed_counts, session, fake_embedder) -> None:
    # Nothing embedded yet -> no eligible rows.
    query_vec = _vec(fake_embedder, "anything")
    assert dense.search(session, query_vec, "documents", top_k=5) == []


def test_dense_search_employee_profiles_returns_employee_id(
    seed_counts, session, fake_embedder
) -> None:
    prof = session.scalars(select(EmployeeProfile).order_by(EmployeeProfile.employee_id)).first()
    prof.embedding = _vec(fake_embedder, "expert network vpn")
    session.flush()
    hits = dense.search(session, _vec(fake_embedder, "expert network vpn"), "employee_profiles", 5)
    assert hits and hits[0][0] == prof.employee_id
    assert isinstance(hits[0][0], int)


def test_dense_search_rejects_unknown_target(seed_counts, session, fake_embedder) -> None:
    with pytest.raises(ValueError, match="unknown target"):
        dense.search(session, _vec(fake_embedder, "x"), "widgets", 5)


# --------------------------------------------------------------------------- #
# indexing (embed_corpus)
# --------------------------------------------------------------------------- #
def test_embed_corpus_fills_missing_embeddings(seed_counts, session, fake_embedder) -> None:
    n_answers_with_body = session.scalars(select(Answer).where(Answer.body.isnot(None))).all()
    expected_answers = len([a for a in n_answers_with_body if (a.body or "").strip()])

    counts = embed_corpus(session, fake_embedder)

    assert counts["documents"] == 30
    assert counts["answers"] == expected_answers
    assert counts["questions"] >= 1
    assert counts["employee_profiles"] >= 1
    # Every answer that had text now carries a vector.
    assert all(a.embedding is not None for a in n_answers_with_body if (a.body or "").strip())


def test_embed_corpus_only_missing_is_idempotent(seed_counts, session, fake_embedder) -> None:
    first = embed_corpus(session, fake_embedder)
    assert sum(first.values()) > 0
    second = embed_corpus(session, fake_embedder)  # only_missing default -> nothing left
    assert sum(second.values()) == 0


def test_embed_corpus_reembeds_when_not_only_missing(seed_counts, session, fake_embedder) -> None:
    embed_corpus(session, fake_embedder)
    again = embed_corpus(session, fake_embedder, only_missing=False)
    assert again["documents"] == 30  # re-embedded despite existing vectors


# --------------------------------------------------------------------------- #
# HybridRetriever end-to-end
# --------------------------------------------------------------------------- #
def test_hybrid_retriever_end_to_end(seed_counts, session, fake_embedder) -> None:
    embed_corpus(session, fake_embedder)  # populate dense vectors for every row

    target = session.scalars(select(Answer).where(Answer.body.isnot(None))).first()
    query = target.body  # dense: exact vector match; sparse: shares tokens

    retriever = HybridRetriever(fake_embedder, session, top_k=5)
    result = retriever.search(query)

    assert set(result) == {"past_answers", "documents", "candidate_people"}

    past = result["past_answers"]
    assert past, "expected at least one fused past answer"
    top = past[0]
    assert set(top) == {"qa_id", "score", "responder_id"}
    assert top["score"] > 0
    assert isinstance(top["responder_id"], int)
    # The exact-match answer is retrieved (fixtures reuse answer bodies, so
    # several rows can tie on an exact query; assert membership, not rank 0).
    assert target.id in {p["qa_id"] for p in past}

    # documents section is well-formed (may be empty for this query)
    assert isinstance(result["documents"], list)
    for doc in result["documents"]:
        assert set(doc) == {"doc_id", "score"}

    # candidate people are employee ids; the top answer's responder leads.
    people = result["candidate_people"]
    assert people and all(isinstance(p, int) for p in people)
    assert people[0] == top["responder_id"]
    assert len(people) == len(set(people))  # de-duplicated
    assert len(people) <= 5


def test_hybrid_retriever_document_channel(seed_counts, session, fake_embedder) -> None:
    embed_corpus(session, fake_embedder)
    doc = session.scalars(select(Document).where(Document.body.isnot(None))).first()

    retriever = HybridRetriever(fake_embedder, session, top_k=5)
    result = retriever.search(f"{doc.title or ''} {doc.body or ''}")

    doc_ids = [d["doc_id"] for d in result["documents"]]
    assert doc.id in doc_ids


def test_hybrid_retriever_respects_top_k(seed_counts, session, fake_embedder) -> None:
    embed_corpus(session, fake_embedder)
    q = session.scalars(select(Question).where(Question.body.isnot(None))).first()
    retriever = HybridRetriever(fake_embedder, session, top_k=2)
    result = retriever.search(q.body)
    assert len(result["past_answers"]) <= 2
    assert len(result["documents"]) <= 2
    assert len(result["candidate_people"]) <= 2
