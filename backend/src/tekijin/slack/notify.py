"""The "tell the other party" step after a chat message is saved.

Shared by every entry point into the Slack integration (#388, #hand-off-chat):
a message can originate from ``POST /messages`` (TEKIJIN -> Slack), a Slack DM
reply routed in by ``POST /slack/events`` (Slack -> TEKIJIN), or the hand-off
draft auto-seeded as a thread's first message when a responder accepts
(``AgentService._record_outcome``) — every one of these needs the same "did
the OTHER party link Slack? stamp last_notified_thread_id, send the DM"
handling once the row is in the ``messages`` table.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from tekijin.config import get_settings
from tekijin.data.slack_links import SlackLink, get_slack_link
from tekijin.slack.client import send_dm


def notify_recipient_via_slack(
    *, recipient_slack_user_id: str, sender_name: str, body: str, thread_id: int
) -> None:
    """Best-effort DM: composed here so every caller sends identical wording."""

    settings = get_settings()
    link = f"{settings.slack_frontend_url.rstrip('/')}/chat?thread={thread_id}"
    preview = body if len(body) <= 200 else f"{body[:200]}…"
    text = f"{sender_name}さんからTEKIJINでメッセージが届きました:\n{preview}\n{link}"
    send_dm(bot_token=settings.slack_bot_token, slack_user_id=recipient_slack_user_id, text=text)


@dataclass(frozen=True)
class _Recipient:
    link: SlackLink
    sender_name: str


def _resolve_recipient(
    session: Session, *, parties: dict, sender_id: int, thread_id: int
) -> _Recipient | None:
    """The OTHER party's Slack link, if notifications are on and they have one.

    Also stamps ``last_notified_thread_id`` — this is what lets a reply typed
    directly in Slack (#388) find its way back to the right TEKIJIN thread, so
    it must happen regardless of whether the caller sends synchronously or via
    a background task.
    """

    settings = get_settings()
    if not settings.slack_notifications_enabled():
        return None
    is_asker = sender_id == parties["asker_id"]
    recipient_id = parties["responder_id"] if is_asker else parties["asker_id"]
    sender_name = parties["asker_name"] if is_asker else parties["responder_name"]
    recipient_link = get_slack_link(session, recipient_id)
    if recipient_link is None:
        return None
    recipient_link.last_notified_thread_id = thread_id
    return _Recipient(link=recipient_link, sender_name=sender_name)


def maybe_notify_via_slack(
    session: Session,
    background_tasks: BackgroundTasks,
    *,
    parties: dict,
    sender_id: int,
    body: str,
    thread_id: int,
) -> None:
    """Like :func:`notify_via_slack_now`, but deferred to a background task —
    for callers that run inside a FastAPI request/response cycle, where
    ``BackgroundTasks`` keeps the Slack API call from delaying the response.
    """

    recipient = _resolve_recipient(
        session, parties=parties, sender_id=sender_id, thread_id=thread_id
    )
    if recipient is None:
        return
    background_tasks.add_task(
        notify_recipient_via_slack,
        recipient_slack_user_id=recipient.link.slack_user_id,
        sender_name=recipient.sender_name,
        body=body,
        thread_id=thread_id,
    )


def notify_via_slack_now(
    session: Session, *, parties: dict, sender_id: int, body: str, thread_id: int
) -> None:
    """Like :func:`maybe_notify_via_slack`, but sent immediately (synchronously)
    — for callers with no ``BackgroundTasks`` to defer to (``AgentService``'s
    outcome recording runs outside any single FastAPI request's lifecycle, so
    there is nothing to attach a background task to there).
    """

    recipient = _resolve_recipient(
        session, parties=parties, sender_id=sender_id, thread_id=thread_id
    )
    if recipient is None:
        return
    notify_recipient_via_slack(
        recipient_slack_user_id=recipient.link.slack_user_id,
        sender_name=recipient.sender_name,
        body=body,
        thread_id=thread_id,
    )
