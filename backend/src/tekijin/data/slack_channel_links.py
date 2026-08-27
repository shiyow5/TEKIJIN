"""Read/write for the shared Slack channel between one pair of employees
(#hand-off-chat)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from tekijin.models.tables import SlackChannelLink


def _canonical_pair(employee_a: int, employee_b: int) -> tuple[int, int]:
    return (employee_a, employee_b) if employee_a < employee_b else (employee_b, employee_a)


def get_channel_link(session: Session, employee_a: int, employee_b: int) -> SlackChannelLink | None:
    low, high = _canonical_pair(employee_a, employee_b)
    return session.get(SlackChannelLink, (low, high))


def get_channel_link_by_channel_id(
    session: Session, slack_channel_id: str
) -> SlackChannelLink | None:
    """Reverse lookup for an inbound Slack event (#hand-off-chat): which pair
    (and, via ``current_thread_id``, which thread) does this channel belong to?"""

    return session.scalar(
        select(SlackChannelLink).where(SlackChannelLink.slack_channel_id == slack_channel_id)
    )


def create_channel_link(
    session: Session,
    employee_a: int,
    employee_b: int,
    *,
    thread_id: int,
    slack_channel_id: str,
    slack_team_id: str,
    now: dt.datetime,
) -> SlackChannelLink:
    low, high = _canonical_pair(employee_a, employee_b)
    # Upsert, not insert: ``(employee_low_id, employee_high_id)`` is the primary
    # key, so a pair can hold only one channel. A caller that decided to REPLACE
    # an existing channel (``ensure_pair_channel`` discards one from a different
    # workspace) would otherwise hit a duplicate-key error — inside a best-effort
    # daemon thread, which swallows it, leaving that pair with no Slack channel
    # for good. Mirrors ``upsert_slack_link``.
    link = session.get(SlackChannelLink, (low, high))
    if link is None:
        link = SlackChannelLink(
            employee_low_id=low,
            employee_high_id=high,
            slack_channel_id=slack_channel_id,
            slack_team_id=slack_team_id,
            current_thread_id=thread_id,
            created_at=now,
        )
        session.add(link)
        return link
    link.slack_channel_id = slack_channel_id
    link.slack_team_id = slack_team_id
    link.current_thread_id = thread_id
    link.created_at = now
    return link
