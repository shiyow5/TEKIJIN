"""Per-message Slack-thread provenance (#476/#508).

Records which TEKIJIN thread a specific Slack message belonged to, so a later
✅ reaction on that message can be attributed to the RIGHT thread — not merely the
pair channel's most recent one (``SlackChannelLink.current_thread_id``), which is
wrong on a channel reused across sequential hand-offs.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from tekijin.models.tables import SlackMessageAnchor


def record_message_anchor(
    session: Session,
    *,
    slack_channel_id: str,
    slack_ts: str,
    thread_id: int,
    now: dt.datetime,
) -> None:
    """Upsert ``(channel, ts) -> thread_id`` (idempotent on the message identity).

    Re-recording the same Slack message refreshes the thread it maps to (a message
    ts never legitimately changes threads, but the upsert keeps redelivery / replay
    harmless). The caller owns the transaction.
    """

    anchor = session.get(SlackMessageAnchor, (slack_channel_id, slack_ts))
    if anchor is None:
        session.add(
            SlackMessageAnchor(
                slack_channel_id=slack_channel_id,
                slack_ts=slack_ts,
                thread_id=thread_id,
                created_at=now,
            )
        )
    else:
        anchor.thread_id = thread_id


def thread_for_message(session: Session, slack_channel_id: str, slack_ts: str) -> int | None:
    """The TEKIJIN thread a given Slack message belongs to, or ``None`` if unknown
    (never mirrored, e.g. a bot post or a message posted before capture was on)."""

    anchor = session.get(SlackMessageAnchor, (slack_channel_id, slack_ts))
    return anchor.thread_id if anchor is not None else None
