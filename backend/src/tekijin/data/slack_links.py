"""Read/write for per-employee Slack account links (chat -> Slack DM notification)."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import SlackLink

logger = logging.getLogger(__name__)


def _matches_team(link: SlackLink | None, expected_team_id: str) -> SlackLink | None:
    """Drop a link that belongs to a different Slack workspace (#406).

    The OAuth callback rejects a foreign workspace, but rows written BEFORE
    ``slack_team_id`` was configured are already in the table. Filtering on READ
    covers those without a migration, and self-heals: re-linking from the right
    workspace overwrites the row. A blank ``expected_team_id`` accepts anything,
    matching the callback's "not configured" behaviour.
    """

    if link is None or not expected_team_id:
        return link
    if link.slack_team_id == expected_team_id:
        return link
    # Loud on purpose: a typo in TEKIJIN_SLACK_TEAM_ID turns every linked
    # employee into "not linked" across the whole app, and without this there is
    # nothing in the log to explain it.
    logger.warning(
        "Ignoring Slack link for employee %s: workspace %s != configured %s",
        link.employee_id,
        link.slack_team_id,
        expected_team_id,
    )
    return None


def get_slack_link(
    session: Session, employee_id: int, *, expected_team_id: str = ""
) -> SlackLink | None:
    return _matches_team(session.get(SlackLink, employee_id), expected_team_id)


def get_slack_link_by_slack_user_id(
    session: Session, slack_user_id: str, *, expected_team_id: str = ""
) -> SlackLink | None:
    """Reverse lookup for an inbound Slack event (#388): which employee is this
    Slack user linked to?"""

    return _matches_team(
        session.scalar(select(SlackLink).where(SlackLink.slack_user_id == slack_user_id)),
        expected_team_id,
    )


def upsert_slack_link(
    session: Session,
    employee_id: int,
    *,
    slack_user_id: str,
    slack_team_id: str,
    now: dt.datetime,
) -> None:
    """Create or replace ``employee_id``'s Slack link (re-linking overwrites it)."""

    link = session.get(SlackLink, employee_id)
    if link is None:
        session.add(
            SlackLink(
                employee_id=employee_id,
                slack_user_id=slack_user_id,
                slack_team_id=slack_team_id,
                linked_at=now,
            )
        )
    else:
        link.slack_user_id = slack_user_id
        link.slack_team_id = slack_team_id
        link.linked_at = now


def delete_slack_link(session: Session, employee_id: int) -> None:
    link = session.get(SlackLink, employee_id)
    if link is not None:
        session.delete(link)
