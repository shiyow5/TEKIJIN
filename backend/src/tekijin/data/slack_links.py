"""Read/write for per-employee Slack account links (chat -> Slack DM notification)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import SlackLink


def get_slack_link(session: Session, employee_id: int) -> SlackLink | None:
    return session.get(SlackLink, employee_id)


def get_slack_link_by_slack_user_id(session: Session, slack_user_id: str) -> SlackLink | None:
    """Reverse lookup for an inbound Slack event (#388): which employee is this
    Slack user linked to?"""

    return session.scalar(select(SlackLink).where(SlackLink.slack_user_id == slack_user_id))


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
