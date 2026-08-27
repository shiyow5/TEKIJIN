"""Departure has to reach the recommender, not just the login (#506).

Unlinking a departed colleague's Slack account stops them signing in. It does
NOT stop them being recommended: the candidate pool is the whole roster
(`nodes.py` `_candidate_pool` -> `list_employees()`), not an evidence-driven
subset, so a person who left still gets scored and can still be put in front of
someone as "ask them". The hand-off then goes nowhere, because the link that
would have delivered it is exactly what was removed.

`is_active` defaults to TRUE, on the column and in the DDL, because the 40
synthetic colleagues predate it and every one of them must stay in the pool —
a default of false would empty the recommender and take the eval numbers with it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, update

from tekijin.data.db import get_sessionmaker
from tekijin.data.repository import Repository
from tekijin.models.tables import Employee


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


def test_no_colleague_is_left_without_an_answer(seed_counts, engine) -> None:
    """The migration adds the column to a database that already HAS rows, so the
    default has to reach those rows, not only new inserts — a NULL here would
    make ``is_active IS TRUE`` drop a working colleague out of the pool.

    Asserts "no NULLs" rather than "all true" on purpose: other tests in this
    suite deactivate people and commit, so "all true" would pass or fail on
    collection order rather than on the migration.
    """

    with get_sessionmaker(engine)() as session:
        unanswered = session.scalar(
            select(func.count()).select_from(Employee).where(Employee.is_active.is_(None))
        )
        total = session.scalar(select(func.count()).select_from(Employee))

    assert total
    assert unanswered == 0


def test_a_new_row_is_active_without_being_told_to_be(seed_counts, engine) -> None:
    """The column's own default, independent of anything the callers pass."""

    factory = get_sessionmaker(engine)
    with factory() as session:
        session.add(Employee(id=9401, name="既定確認", email="default-check@x.jp"))
        session.commit()

    with factory() as session:
        assert session.get(Employee, 9401).is_active is True


def test_list_employees_excludes_someone_who_has_left(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        target = session.scalars(select(Employee).order_by(Employee.id)).first()
        assert target is not None
        target_id = target.id
        before = len(Repository(session).list_employees())
        target.is_active = False
        session.commit()

    with factory() as session:
        listed = Repository(session).list_employees()

    assert len(listed) == before - 1
    assert target_id not in {e.id for e in listed}


def test_a_departed_colleague_is_still_readable_by_id(seed_counts, engine) -> None:
    """Only the CANDIDATE POOL shrinks. Their name still has to resolve, or every
    question and answer they left behind loses its author in the history."""

    factory = get_sessionmaker(engine)
    with factory() as session:
        target = session.scalars(select(Employee).order_by(Employee.id)).first()
        assert target is not None
        target_id = target.id
        target.is_active = False
        session.commit()

    with factory() as session:
        assert Repository(session).get_employee(target_id) is not None
