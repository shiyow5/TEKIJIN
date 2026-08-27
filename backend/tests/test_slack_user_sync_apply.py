"""The database half of the directory sync (#406 step 3).

The planner decides; this only carries out. The tests here are about the
carrying-out being faithful and idempotent — running the same sync twice must
not produce a second round of writes, because it runs on a schedule.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from tekijin.data.db import get_sessionmaker
from tekijin.data.slack_directory import apply_sync_plan, load_directory_state
from tekijin.data.slack_links import get_slack_link, upsert_slack_link
from tekijin.models.tables import Employee
from tekijin.slack.user_sync import SyncPlan

NOW = dt.datetime(2026, 9, 20, 9, 0, 0)
TEAM = "T_REAL"

# Dedicated rows, far above the 40 seeded ids. Twice in this codebase a Slack
# test has quietly depended on another file not having claimed the same employee
# id, and both times it only passed because pytest happened to collect the files
# in a helpful order. Creating our own removes the coupling instead of dodging it.
_BASE_ID = 9100


def _employee(session, offset: int) -> int:
    employee_id = _BASE_ID + offset
    if session.get(Employee, employee_id) is None:
        session.add(
            Employee(
                id=employee_id,
                name=f"同期テスト{offset}",
                email=f"sync-test-{offset}@sample-tekijin.co.jp",
            )
        )
        session.flush()
    return employee_id


def test_load_directory_state_reads_emails_and_existing_links(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        employee_id = _employee(session, 1)
        upsert_slack_link(session, employee_id, slack_user_id="U_S1", slack_team_id=TEAM, now=NOW)
        session.commit()

    with factory() as session:
        state = load_directory_state(session)

    assert state.linked_slack_user_by_employee[employee_id] == "U_S1"
    assert state.employee_by_slack_user["U_S1"] == employee_id
    # Emails are the join key, so they must be normalised on the way in — a
    # fixture address in mixed case would otherwise never match Slack's.
    assert all(email == email.strip().lower() for email in state.employee_id_by_email)
    assert employee_id in state.employee_id_by_email.values()


def test_apply_writes_the_links_the_plan_asked_for(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        employee_id = _employee(session, 2)
        session.commit()
    plan = SyncPlan(link=((employee_id, "U_S2"),))

    with factory() as session:
        applied = apply_sync_plan(session, plan, team_id=TEAM, now=NOW)
        session.commit()

    assert applied == {"created": 0, "linked": 1, "unlinked": 0}
    with factory() as session:
        link = get_slack_link(session, employee_id, expected_team_id=TEAM)
        assert link is not None
        assert link.slack_user_id == "U_S2"
        # Stamped with the CONFIGURED workspace, never one Slack claimed in a
        # payload — the read-time filter is only as good as what was written.
        assert link.slack_team_id == TEAM


def test_apply_removes_the_links_the_plan_retired(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        employee_id = _employee(session, 3)
        upsert_slack_link(session, employee_id, slack_user_id="U_S3", slack_team_id=TEAM, now=NOW)
        session.commit()

    with factory() as session:
        applied = apply_sync_plan(session, SyncPlan(unlink=(employee_id,)), team_id=TEAM, now=NOW)
        session.commit()

    assert applied == {"created": 0, "linked": 0, "unlinked": 1}
    with factory() as session:
        assert get_slack_link(session, employee_id, expected_team_id=TEAM) is None


def test_applying_the_same_plan_twice_is_idempotent(seed_counts, engine) -> None:
    """It runs on a schedule, so "again" is the normal case, not the edge case."""

    factory = get_sessionmaker(engine)
    with factory() as session:
        employee_id = _employee(session, 4)
        session.commit()
    plan = SyncPlan(link=((employee_id, "U_S4"),))

    with factory() as session:
        apply_sync_plan(session, plan, team_id=TEAM, now=NOW)
        session.commit()
    with factory() as session:
        apply_sync_plan(session, plan, team_id=TEAM, now=NOW)
        session.commit()

    with factory() as session:
        link = get_slack_link(session, employee_id, expected_team_id=TEAM)
        assert link is not None
        assert link.slack_user_id == "U_S4"


def test_an_empty_plan_touches_nothing(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        assert apply_sync_plan(session, SyncPlan(), team_id=TEAM, now=NOW) == {
            "created": 0,
            "linked": 0,
            "unlinked": 0,
        }


# --------------------------------------------------------------------------- #
# Defence in depth: the applier does not trust the plan
# --------------------------------------------------------------------------- #
# The planner now refuses an ambiguous pair, so a duplicated plan should be
# unreachable. These exist because "unreachable" was the assumption that let the
# same-run collision ship in the first place: `upsert_slack_link` keys on
# employee_id and overwrites, so a duplicate would silently resolve to whichever
# entry happened to be last — the quietest possible way to put one person's
# Slack identity on another person's row.
def test_a_plan_naming_one_employee_twice_is_refused_before_anything_is_written(
    seed_counts, engine
) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        employee_id = _employee(session, 5)
        session.commit()

    plan = SyncPlan(link=((employee_id, "U_FIRST"), (employee_id, "U_SECOND")))

    with factory() as session:
        with pytest.raises(ValueError, match="duplicate"):
            apply_sync_plan(session, plan, team_id=TEAM, now=NOW)
        session.rollback()

    # Refused BEFORE writing, so not even the first entry landed.
    with factory() as session:
        assert get_slack_link(session, employee_id, expected_team_id=TEAM) is None


def test_a_plan_naming_one_slack_account_twice_is_refused(seed_counts, engine) -> None:
    """This one would otherwise hit the unique constraint mid-batch and roll the
    whole transaction back — losing the departure unlinks queued alongside it."""

    factory = get_sessionmaker(engine)
    with factory() as session:
        first = _employee(session, 6)
        second = _employee(session, 7)
        session.commit()

    plan = SyncPlan(link=((first, "U_SHARED"), (second, "U_SHARED")))

    with factory() as session:
        with pytest.raises(ValueError, match="duplicate"):
            apply_sync_plan(session, plan, team_id=TEAM, now=NOW)
        session.rollback()


# --------------------------------------------------------------------------- #
# Creating a colleague who is in Slack but not yet in TEKIJIN
# --------------------------------------------------------------------------- #
def test_a_new_colleague_is_created_and_linked_in_one_go(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    plan = SyncPlan(create=(("newhire@sample-tekijin.co.jp", "新人 太郎", "U_NEW"),))

    with factory() as session:
        applied = apply_sync_plan(session, plan, team_id=TEAM, now=NOW)
        session.commit()

    assert applied["created"] == 1
    with factory() as session:
        from tekijin.data.slack_links import get_slack_link_by_slack_user_id

        link = get_slack_link_by_slack_user_id(session, "U_NEW", expected_team_id=TEAM)
        assert link is not None
        created = session.get(Employee, link.employee_id)
        assert created is not None
        assert created.email == "newhire@sample-tekijin.co.jp"
        assert created.name == "新人 太郎"
        # No password: this account exists to be signed into via Slack. A blank
        # hash never verifies, so there is no weak credential to guess.
        assert not created.password_hash


def test_the_created_id_does_not_collide_with_the_seeded_roster(seed_counts, engine) -> None:
    """`make seed` inserts ids 1..40 EXPLICITLY after `TRUNCATE ... RESTART
    IDENTITY`, so the sequence is left at 1 while `max(id)` is 40. Letting
    Postgres assign the next value would hand out 1 and collide with the first
    employee. Verified on the live DGX database before writing this.
    """

    factory = get_sessionmaker(engine)
    with factory() as session:
        before = {e.id for e in session.scalars(select(Employee))}

    with factory() as session:
        apply_sync_plan(
            session,
            SyncPlan(create=(("collide@sample-tekijin.co.jp", "衝突 確認", "U_C"),)),
            team_id=TEAM,
            now=NOW,
        )
        session.commit()

    with factory() as session:
        after = {e.id for e in session.scalars(select(Employee))}

    new_ids = after - before
    assert len(new_ids) == 1
    assert new_ids.isdisjoint(before)


def test_creating_two_colleagues_in_one_run_gives_them_distinct_ids(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    plan = SyncPlan(
        create=(
            ("a-two@sample-tekijin.co.jp", "A", "U_TWO_A"),
            ("b-two@sample-tekijin.co.jp", "B", "U_TWO_B"),
        )
    )

    with factory() as session:
        applied = apply_sync_plan(session, plan, team_id=TEAM, now=NOW)
        session.commit()

    assert applied["created"] == 2
    with factory() as session:
        from tekijin.data.slack_links import get_slack_link_by_slack_user_id

        a = get_slack_link_by_slack_user_id(session, "U_TWO_A", expected_team_id=TEAM)
        b = get_slack_link_by_slack_user_id(session, "U_TWO_B", expected_team_id=TEAM)
        assert a is not None and b is not None
        assert a.employee_id != b.employee_id


def test_a_plan_creating_one_address_twice_is_refused(seed_counts, engine) -> None:
    """`employees.email` is unique, so a duplicate would abort the batch."""

    factory = get_sessionmaker(engine)
    plan = SyncPlan(
        create=(
            ("dup@sample-tekijin.co.jp", "A", "U_D1"),
            ("dup@sample-tekijin.co.jp", "B", "U_D2"),
        )
    )

    with factory() as session:
        with pytest.raises(ValueError, match="duplicate"):
            apply_sync_plan(session, plan, team_id=TEAM, now=NOW)
        session.rollback()


def test_a_departure_also_takes_them_out_of_the_candidate_pool(seed_counts, engine) -> None:
    """Unlinking stops the login; it does not stop the recommendation (#506).
    Both have to happen, or the recommender keeps offering someone whose
    hand-off can no longer reach them."""

    factory = get_sessionmaker(engine)
    with factory() as session:
        employee_id = _employee(session, 8)
        upsert_slack_link(session, employee_id, slack_user_id="U_LEFT", slack_team_id=TEAM, now=NOW)
        session.commit()

    with factory() as session:
        apply_sync_plan(session, SyncPlan(unlink=(employee_id,)), team_id=TEAM, now=NOW)
        session.commit()

    with factory() as session:
        gone = session.get(Employee, employee_id)
        assert gone is not None, "the row must survive — history references it"
        assert gone.is_active is False
        assert get_slack_link(session, employee_id, expected_team_id=TEAM) is None


def test_a_created_colleague_starts_active(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        apply_sync_plan(
            session,
            SyncPlan(create=(("active@sample-tekijin.co.jp", "現役", "U_ACT"),)),
            team_id=TEAM,
            now=NOW,
        )
        session.commit()

    with factory() as session:
        row = session.scalars(
            select(Employee).where(Employee.email == "active@sample-tekijin.co.jp")
        ).one()
        assert row.is_active is True
