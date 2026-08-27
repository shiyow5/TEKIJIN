"""A departed colleague must leave the pool the recommender ACTUALLY uses (#506).

The first attempt at this filtered `Repository.list_employees()`, on the belief
that it was the candidate pool. It is not, in the shipped configuration:
`nodes._candidate_pool` only consults it when `score_all_employees` is on, and
that setting defaults to False (ADR-0009 measured and rejected it). By default
the pool is `retrieval["candidate_people"]`, built by the retriever out of
`list_answers()` and `list_profiles()` — neither of which knew about
`is_active`. So the filter was real, and it was wired to nothing.

These tests go through the retriever the product actually runs, so they cannot
pass while the pool is built somewhere the filter does not reach.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from tekijin.data.db import get_sessionmaker
from tekijin.models.tables import Answer, Employee
from tekijin.retrieval.retriever import HybridRetriever


@pytest.fixture(autouse=True)
def _reactivate_everyone(engine):
    """Put `is_active` back after each test in this module.

    These tests deactivate a SEEDED colleague and commit, and the database is
    shared across the whole run — so without this the person stays missing from
    the candidate pool and unrelated retrieval tests start failing depending on
    collection order. (They did: `test_hybrid_retriever_end_to_end` and two
    others, only when this module ran first.)
    """

    yield
    with get_sessionmaker(engine)() as session:
        session.execute(update(Employee).values(is_active=True))
        session.commit()


def _responder_of_first_answer(session) -> tuple[str, int]:
    answer = session.scalars(select(Answer).order_by(Answer.id)).first()
    assert answer is not None
    return answer.id, answer.responder_id


def _search(engine, fake_embedder, query: str):
    with get_sessionmaker(engine)() as session:
        return HybridRetriever(fake_embedder, session).search(query)


def test_a_departed_responder_is_not_offered_as_a_candidate(
    seed_counts, engine, fake_embedder
) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        answer_id, responder_id = _responder_of_first_answer(session)
        question_text = session.scalars(select(Answer).where(Answer.id == answer_id)).one().body

    before = _search(engine, fake_embedder, question_text)["candidate_people"]
    assert responder_id in before, (
        "precondition: this responder must be reachable before deactivation, "
        "or the test proves nothing"
    )

    with factory() as session:
        session.get(Employee, responder_id).is_active = False
        session.commit()

    after = _search(engine, fake_embedder, question_text)["candidate_people"]
    assert responder_id not in after


def test_the_remaining_colleagues_are_still_offered(seed_counts, engine, fake_embedder) -> None:
    """Deactivating one person must not empty the pool — that would look like a
    working filter while actually breaking the recommender for everyone."""

    factory = get_sessionmaker(engine)
    with factory() as session:
        answer = session.scalars(select(Answer).order_by(Answer.id)).first()
        question_text = answer.body
        responder_id = answer.responder_id

    with factory() as session:
        session.get(Employee, responder_id).is_active = False
        session.commit()

    after = _search(engine, fake_embedder, question_text)["candidate_people"]
    assert after, "the pool must not be emptied by one departure"


def test_the_prior_answer_pin_does_not_seat_a_departed_responder() -> None:
    """`prior_answer` pins the past responder at rank 1, which bypasses the pool
    entirely. Filtering the pool without filtering the pin would leave the one
    path that puts a name in front of the asker with the highest possible
    prominence still able to name someone who has left.
    """

    from tekijin.agent.nodes import AgentNodes

    state = {
        "retrieval": {
            "past_answers": [
                {"answer_id": "A1", "responder_id": 7, "score": 0.9},
                {"answer_id": "A2", "responder_id": 8, "score": 0.5},
            ],
            # 7 has left. Note they are still in `candidate_people` here on
            # purpose: the pin must be stopped by the departure signal itself,
            # not by absence from the pool, which it is allowed to ignore.
            "candidate_people": [7, 8],
            "departed_people": [7],
        }
    }

    pinned = AgentNodes.prior_answer(object(), state)["pinned_responder_id"]

    assert pinned != 7


def test_the_prior_answer_pin_still_works_for_someone_present() -> None:
    """The guard must not disable the pin outright — that would quietly drop the
    "ask the person who answered this before" behaviour for everyone."""

    from tekijin.agent.nodes import AgentNodes

    state = {
        "retrieval": {
            "past_answers": [{"answer_id": "A1", "responder_id": 7, "score": 0.9}],
            # Deliberately NOT in the pool — the pin is supposed to reach past it.
            "candidate_people": [8],
            "departed_people": [],
        }
    }

    assert AgentNodes.prior_answer(object(), state)["pinned_responder_id"] == 7


def test_the_retriever_reports_departed_responders_to_the_pin(
    seed_counts, engine, fake_embedder
) -> None:
    """The pin's guard is only as good as the signal reaching it.

    Written after the wiring was missing: `departed_people` was computed in the
    retriever and never returned, and the pin test still passed because it
    supplied the value itself. Ruff noticed the unused variable; no test did.
    """

    factory = get_sessionmaker(engine)
    with factory() as session:
        answer = session.scalars(select(Answer).order_by(Answer.id)).first()
        question_text, responder_id = answer.body, answer.responder_id

    assert responder_id not in _search(engine, fake_embedder, question_text).get(
        "departed_people", []
    )

    with factory() as session:
        session.get(Employee, responder_id).is_active = False
        session.commit()

    assert responder_id in _search(engine, fake_embedder, question_text)["departed_people"]
