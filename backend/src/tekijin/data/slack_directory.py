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
    """

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
        logger.info(
            "Slack directory sync unlinked employee %s (deactivated in Slack)",
            employee_id,
        )

    return {"linked": len(plan.link), "unlinked": len(plan.unlink)}
