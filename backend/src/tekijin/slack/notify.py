"""Bridges chat threads to a shared per-pair Slack channel (#hand-off-chat, #388).

A "chat" hand-off, once accepted with BOTH the asker and responder Slack-linked,
gets a private Slack channel (bot + the two of them) — created the first time
that pair consults each other via chat, then REUSED for every later hand-off
between the same two people (:func:`ensure_pair_channel`), so consulting the
same colleague again doesn't pile up a fresh channel every time.

Because both humans are members of the same channel, Slack-to-Slack delivery
is native (Slack does that); only the TEKIJIN <-> Slack edges
(:func:`relay_to_channel`, ``POST /slack/events``) are this module's job.
Which TEKIJIN thread an inbound Slack message is attributed to is
``SlackChannelLink.current_thread_id`` — see that model's docstring for the
"most recent thread wins" trade-off channel reuse implies.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading

from sqlalchemy.orm import Session, sessionmaker

from tekijin.config import get_settings
from tekijin.data.db import session_scope
from tekijin.data.slack_channel_links import create_channel_link, get_channel_link
from tekijin.data.slack_links import get_slack_link
from tekijin.slack.client import create_private_channel, invite_to_channel, post_message

logger = logging.getLogger(__name__)

# Well under Slack's chat.postMessage text limit — long enough that a normal
# chat message or hand-off draft is never touched, short enough that an
# accidentally-pasted log dump doesn't silently fail to post at all (the
# DM-based predecessor this module replaced capped at the same length).
_MAX_TEXT_LENGTH = 3000


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_TEXT_LENGTH else f"{text[:_MAX_TEXT_LENGTH]}…"


def ensure_pair_channel(session: Session, *, thread_id: int, parties: dict) -> str | None:
    """Create (once per pair) or reuse the Slack channel for this hand-off's
    two parties, stamping ``current_thread_id`` to ``thread_id`` either way.

    Only when BOTH parties are Slack-linked — a channel with just one human
    plus the bot isn't meaningfully different from a DM, so it isn't worth
    the extra Slack API calls or channel-list clutter. Returns ``None`` (no
    channel) if notifications are off, either party is unlinked, or any Slack
    API call fails; every step is best-effort and logs its own failure.
    """

    settings = get_settings()
    if not settings.slack_notifications_enabled():
        # Checked BEFORE the reuse lookup below: an existing channel from
        # before the bot token was unset must not still be posted to with an
        # empty token.
        return None

    asker_id, responder_id = parties["asker_id"], parties["responder_id"]
    existing = get_channel_link(session, asker_id, responder_id)
    if existing is not None:
        existing.current_thread_id = thread_id
        return existing.slack_channel_id

    asker_link = get_slack_link(session, asker_id)
    responder_link = get_slack_link(session, responder_id)
    if asker_link is None or responder_link is None:
        return None

    # Named after the pair, not the thread, so a later reuse doesn't need a
    # NEW channel name — Slack channel names must be unique workspace-wide.
    channel_id = create_private_channel(
        bot_token=settings.slack_bot_token,
        name=f"tekijin-{min(asker_id, responder_id)}-{max(asker_id, responder_id)}",
    )
    if channel_id is None:
        return None
    invited = invite_to_channel(
        bot_token=settings.slack_bot_token,
        channel_id=channel_id,
        user_ids=[asker_link.slack_user_id, responder_link.slack_user_id],
    )
    if not invited:
        # The channel exists but nobody could be added to it — useless, and
        # not persisted, so a later message just tries again from scratch.
        return None
    create_channel_link(
        session,
        asker_id,
        responder_id,
        thread_id=thread_id,
        slack_channel_id=channel_id,
        slack_team_id=asker_link.slack_team_id,
        now=dt.datetime.now(),  # noqa: DTZ005 - naive is intentional, matches created_at elsewhere
    )
    return channel_id


def relay_to_channel(
    session_factory: sessionmaker[Session],
    *,
    employee_a: int,
    employee_b: int,
    sender_name: str,
    body: str,
) -> None:
    """Best-effort: post a TEKIJIN-originated message into this pair's shared
    Slack channel, if one already exists. No-op if it doesn't (not both
    parties linked yet, or notifications are off) — this never CREATES a
    channel; only :func:`ensure_pair_channel` (at accept time) does that.
    Does NOT touch ``current_thread_id``: an ordinary message send is not a
    new hand-off, so it must not silently reroute future Slack replies onto
    whichever thread happened to send one last.

    Takes a ``session_factory`` (opens its own session) rather than a live
    ``Session`` — designed to run as a deferred task (a FastAPI
    ``BackgroundTasks`` job, or the fire-and-forget thread below) after the
    caller's own session/transaction has already closed.
    """

    settings = get_settings()
    if not settings.slack_notifications_enabled():
        return
    with session_factory() as session:
        link = get_channel_link(session, employee_a, employee_b)
    if link is None:
        return
    post_message(
        bot_token=settings.slack_bot_token,
        channel_id=link.slack_channel_id,
        text=_truncate(f"{sender_name}: {body}"),
    )


def schedule_channel_setup_and_draft(
    session_factory: sessionmaker[Session], *, thread_id: int, parties: dict, draft: str
) -> None:
    """Fire-and-forget: set up (or reuse) this pair's Slack channel and post
    the hand-off draft into it, without blocking the caller.

    Used from ``AgentService._record_outcome``, which runs synchronously
    inside ``POST /answer``'s request/response cycle but has no
    ``BackgroundTasks`` to defer to (that's a FastAPI request-handler
    concept, and by the time this runs the DB write for the accepted outcome
    has already committed) — channel creation is 2-3 sequential Slack API
    calls, so running it inline would hold that response (and the
    per-session lock ``submit_resume`` takes) for however long Slack takes.
    A plain daemon thread is enough: every step it calls already swallows its
    own errors and logs them EXCEPT ``session_scope``'s own commit — e.g. two
    hand-offs between the same pair accepted within milliseconds of each
    other can both miss the other's not-yet-committed channel row and race
    to insert one, so the outer ``try`` here exists to make that (and any
    other failure this function's own code can raise) visible in the log
    instead of only as an unhandled-thread exception on stderr.
    """

    def _run() -> None:
        try:
            with session_scope(session_factory) as session:
                had_channel = (
                    get_channel_link(session, parties["asker_id"], parties["responder_id"])
                    is not None
                )
                channel_id = ensure_pair_channel(session, thread_id=thread_id, parties=parties)
            if channel_id is not None and not had_channel:
                settings = get_settings()
                post_message(
                    bot_token=settings.slack_bot_token,
                    channel_id=channel_id,
                    text=_truncate(draft),
                )
        except Exception:  # noqa: BLE001 - background thread boundary, must not crash silently
            logger.warning("Slack hand-off channel setup failed", exc_info=True)

    threading.Thread(target=_run, daemon=True, name="slack-handoff-channel-setup").start()


def schedule_pending_handoff(
    session_factory: sessionmaker[Session],
    *,
    session_id: str,
    recommendation_id: int,
    thread_id: int,
    parties: dict,
    draft: str,
) -> None:
    """Create the pair channel when a chat hand-off is sent and post action buttons."""

    def _run() -> None:
        try:
            with session_scope(session_factory) as session:
                channel_id = ensure_pair_channel(session, thread_id=thread_id, parties=parties)
            if channel_id is None:
                return
            settings = get_settings()
            value_base = {"session_id": session_id, "recommendation_id": recommendation_id}
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(draft)}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "承諾"},
                            "style": "primary",
                            "action_id": "tekijin_accept",
                            "value": json.dumps({**value_base, "outcome": "accepted"}),
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "辞退"},
                            "style": "danger",
                            "action_id": "tekijin_decline",
                            "value": json.dumps({**value_base, "outcome": "declined"}),
                        },
                    ],
                },
            ]
            post_message(
                bot_token=settings.slack_bot_token,
                channel_id=channel_id,
                text=_truncate(draft),
                blocks=blocks,
            )
        except Exception:
            logger.warning("Slack pending hand-off setup failed", exc_info=True)

    threading.Thread(target=_run, daemon=True, name="slack-pending-handoff-setup").start()
