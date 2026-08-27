"""Read the state a Slack directory sync needs, and carry out its plan (#406).

Split from ``tekijin.slack.user_sync`` on purpose: that module decides and this
one writes. Keeping the decision pure is what makes the security rules — an
existing link is never overwritten, a Slack account is never re-pointed at
another employee — assertable without a database, and leaves nothing for this
half to get wrong.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.data.slack_links import delete_slack_link, upsert_slack_link
from tekijin.models.tables import Employee, SlackLink
from tekijin.slack.user_sync import SyncPlan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectoryState:
    """The current mapping, as the planner needs to see it."""

    employee_id_by_email: dict[str, int]
    linked_slack_user_by_employee: dict[int, str]
    employee_by_slack_user: dict[str, int]


def load_directory_state(session: Session) -> DirectoryState:
    """Snapshot who exists and who is already linked.

    Addresses are lower-cased here so the planner compares like with like; Slack
    and the employee fixtures do not agree on case.
    """

    employee_id_by_email: dict[str, int] = {}
    for employee_id, email in session.execute(select(Employee.id, Employee.email)):
        if not email:
            continue
        employee_id_by_email[email.strip().lower()] = employee_id

    linked_slack_user_by_employee: dict[int, str] = {}
    employee_by_slack_user: dict[str, int] = {}
    for link in session.scalars(select(SlackLink)):
        linked_slack_user_by_employee[link.employee_id] = link.slack_user_id
        employee_by_slack_user[link.slack_user_id] = link.employee_id

    return DirectoryState(
        employee_id_by_email=employee_id_by_email,
        linked_slack_user_by_employee=linked_slack_user_by_employee,
        employee_by_slack_user=employee_by_slack_user,
    )


def apply_sync_plan(
    session: Session, plan: SyncPlan, *, team_id: str, now: dt.datetime
) -> dict[str, int]:
    """Carry out ``plan``. Returns what it did, for the caller to log/report.

    ``team_id`` is the CONFIGURED workspace, not one taken from a Slack payload:
    the read-time workspace filter in ``slack_links`` is only as trustworthy as
    the value that was written here.

    Raises ``ValueError`` if the plan names an employee — or a Slack account —
    more than once, before writing anything. The planner already refuses those,
    so this should be unreachable; it is here because "unreachable" is exactly
    the assumption that let a same-run collision through once already.
    ``upsert_slack_link`` keys on ``employee_id`` and overwrites, so a duplicate
    would resolve silently to whichever entry came last — the quietest possible
    way to put one person's Slack identity on another person's row. A duplicated
    ``slack_user_id`` would instead violate the unique index halfway through and
    roll back the whole batch, taking the departure unlinks with it.
    """

    _reject_duplicates(plan)

    created = 0
    for email, display_name, slack_user_id in plan.create:
        employee_id = _create_employee(session, email=email, name=display_name)
        upsert_slack_link(
            session,
            employee_id,
            slack_user_id=slack_user_id,
            slack_team_id=team_id,
            now=now,
        )
        created += 1
        logger.info(
            "Slack directory sync created employee %s (%s) and linked %s",
            employee_id,
            email,
            slack_user_id,
        )

    for employee_id, slack_user_id in plan.link:
        upsert_slack_link(
            session,
            employee_id,
            slack_user_id=slack_user_id,
            slack_team_id=team_id,
            now=now,
        )
        logger.info("Slack directory sync linked employee %s to %s", employee_id, slack_user_id)

    for employee_id in plan.unlink:
        delete_slack_link(session, employee_id)
        # Departure has to reach the recommender too (#506): unlinking only
        # stops the login, and a colleague who has left must stop being offered
        # as someone to ask — the hand-off would have nowhere to go, the link
        # that would carry it being what we just removed. The ROW stays;
        # questions, answers and evidence all reference it.
        departed = session.get(Employee, employee_id)
        if departed is not None:
            departed.is_active = False
        logger.info(
            "Slack directory sync unlinked employee %s (deactivated in Slack)",
            employee_id,
        )

    return {
        "created": created,
        "linked": len(plan.link),
        "unlinked": len(plan.unlink),
    }


def _reject_duplicates(plan: SyncPlan) -> None:
    """Refuse a plan whose links are not one-to-one, before any write happens."""

    employees = [employee_id for employee_id, _ in plan.link]
    slack_users = [slack_user_id for _, slack_user_id in plan.link]
    slack_users += [slack_user_id for _, _, slack_user_id in plan.create]
    emails = [email for email, _, _ in plan.create]
    for label, values in (
        ("employee", employees),
        ("slack account", slack_users),
        ("email", emails),
    ):
        if len(set(values)) != len(values):
            raise ValueError(f"refusing a Slack sync plan with a duplicate {label}: {plan.link}")


def _create_employee(session: Session, *, email: str, name: str) -> int:
    """Insert a colleague who exists in Slack but not yet here, and return the id.

    The id comes from the identity sequence. That is only safe because the seed
    realigns the sequence once it has finished inserting its explicit ids (see
    ``seed.realign_identity_sequences``) — without that the sequence sits at 1
    while ``max(id)`` is 40, measured on the live database, and the first
    auto-assigned employee collides with employee 1.

    Realigning per insert instead, which is what this did first, is NOT
    concurrency-safe: two overlapping syncs read the same ``max(id)``, both
    ``setval`` to it and both receive the same id (reproduced: both got 41, and
    the second INSERT then blocked on the first's uncommitted row). ``nextval``
    on its own is atomic, so leaving the sequence alone is the fix.

    No ``password_hash`` is set. The account exists to be signed into via Slack;
    a NULL hash never verifies, so this creates no guessable credential.
    """

    employee = Employee(name=name, email=email)
    session.add(employee)
    # Needed now rather than at commit: the Slack link references this id, and
    # the next creation in the same batch must not be handed the same one.
    session.flush()
    return employee.id
