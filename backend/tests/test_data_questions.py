"""DB-backed tests for the cross-asker question aggregate (#475 Screen 01).

Uses a topic string unique to this module so the count is deterministic
regardless of any seeded questions sharing the real 22-topic vocabulary.
"""

from __future__ import annotations

import pytest

from tekijin.data.questions import count_similar_prior_askers
from tekijin.models.tables import Employee, Question

# A topic no seed fixture uses, so only this test's rows are ever counted.
TOPIC = "zzz-test-topic-475"
OTHER = "zzz-other-topic-475"
# Employee ids far outside any fixture range to avoid PK collisions in the run.
A1, A2, A3, CUR = 940_101, 940_102, 940_103, 940_104


@pytest.fixture
def _questions(session):
    session.add_all(
        [
            Employee(id=A1, name="A1", email="a1@x"),
            Employee(id=A2, name="A2", email="a2@x"),
            Employee(id=A3, name="A3", email="a3@x"),
            Employee(id=CUR, name="Cur", email="cur@x"),
        ]
    )
    session.add_all(
        [
            # Two DISTINCT other askers in the same area (A2 asked twice -> still one).
            Question(id="q475_a", asker_id=A1, body="q", topics=[TOPIC]),
            Question(id="q475_b", asker_id=A2, body="q", topics=[TOPIC, OTHER]),
            Question(id="q475_c", asker_id=A2, body="q", topics=[TOPIC]),
            # A different area — must not be counted.
            Question(id="q475_d", asker_id=A3, body="q", topics=[OTHER]),
            # The current asker's own question in the same area — excluded.
            Question(id="q475_cur", asker_id=CUR, body="q", topics=[TOPIC]),
        ]
    )
    session.flush()
    return session


def test_counts_distinct_other_askers_in_the_same_topic(_questions) -> None:
    # A1 and A2 (A2 only once despite two rows); A3 is a different topic; the
    # current asker + question are excluded -> 2.
    n = count_similar_prior_askers(
        _questions, [TOPIC], exclude_asker_id=CUR, exclude_question_id="q475_cur"
    )
    assert n == 2


def test_topic_overlap_not_exact_match(_questions) -> None:
    # Querying with the current question's topics [TOPIC] still matches q475_b,
    # whose topics are [TOPIC, OTHER] — overlap, not equality.
    n = count_similar_prior_askers(_questions, [TOPIC], exclude_asker_id=A1)
    # A2 (via q475_b/q475_c) and CUR remain -> 2. A1 excluded, A3 different topic.
    assert n == 2


def test_excludes_the_current_question_by_id(_questions) -> None:
    # Without excluding the asker but excluding q475_a, A1 drops out; A2 and CUR
    # remain -> 2.
    n = count_similar_prior_askers(_questions, [TOPIC], exclude_question_id="q475_a")
    assert n == 2


def test_empty_topics_returns_zero(_questions) -> None:
    assert count_similar_prior_askers(_questions, []) == 0
    assert count_similar_prior_askers(_questions, ["", "  "]) == 0


def test_no_match_returns_zero(_questions) -> None:
    assert count_similar_prior_askers(_questions, ["zzz-nonexistent-topic"]) == 0
