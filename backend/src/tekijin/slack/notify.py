"""The "tell the other party" step after a chat message is saved.

Shared by both directions of the Slack integration (#388): a message can
originate from ``POST /messages`` (TEKIJIN -> Slack) or from a Slack DM reply
routed in by ``POST /slack/events`` (Slack -> TEKIJIN) — either way, once the
row is in the ``messages`` table, the OTHER party needs the same "did they
link Slack? stamp last_notified_thread_id, queue the DM" handling.
"""

from __future__ import annotations

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from tekijin.config import get_settings
from tekijin.data.slack_links import get_slack_link
from tekijin.slack.client import send_dm


def notify_recipient_via_slack(
    *, recipient_slack_user_id: str, sender_name: str, body: str, thread_id: int
) -> None:
    """Best-effort DM: composed here so both callers send identical wording."""

    settings = get_settings()
    link = f"{settings.slack_frontend_url.rstrip('/')}/chat?thread={thread_id}"
    preview = body if len(body) <= 200 else f"{body[:200]}…"
    text = f"{sender_name}さんからTEKIJINでメッセージが届きました:\n{preview}\n{link}"
    send_dm(bot_token=settings.slack_bot_token, slack_user_id=recipient_slack_user_id, text=text)


def maybe_notify_via_slack(
    session: Session,
    background_tasks: BackgroundTasks,
    *,
    parties: dict,
    sender_id: int,
    body: str,
    thread_id: int,
) -> None:
    """If the OTHER party has linked Slack (and notifications are enabled),
    remember this as their most-recently-notified thread and queue the DM."""

    settings = get_settings()
    if not settings.slack_notifications_enabled():
        return
    is_asker = sender_id == parties["asker_id"]
    recipient_id = parties["responder_id"] if is_asker else parties["asker_id"]
    sender_name = parties["asker_name"] if is_asker else parties["responder_name"]
    recipient_link = get_slack_link(session, recipient_id)
    if recipient_link is None:
        return
    recipient_link.last_notified_thread_id = thread_id
    background_tasks.add_task(
        notify_recipient_via_slack,
        recipient_slack_user_id=recipient_link.slack_user_id,
        sender_name=sender_name,
        body=body,
        thread_id=thread_id,
    )
