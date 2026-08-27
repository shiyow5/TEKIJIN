"""Solve-capture (#476 Screen 02): a ✅ on a hand-off thread → a knowledge draft.

The "解決した" button is pressed by almost no one — the moment a thread is solved,
the person has already moved on. So capture must ride the conversation instead: a
participant reacting ✅ on the pair-channel thread is enough to distil the resolved
Q&A into a knowledge unit. The unit lands in the review queue as ``unreviewed`` (the
"下書き箱") — created eagerly, so "no one pressed anything" already leaves a draft to
review later; a future prompt/button just makes reviewing it faster.

Gating (all must hold, else no-op):
- ``slack_solve_capture_enabled`` is on (OFF by default — dormant).
- the reaction is one of :data:`SOLVE_REACTIONS`.
- the channel is a TEKIJIN pair-channel (``SlackChannelLink``) — these are shared
  private channels the bot created, never DMs, so no DM ever reaches extraction and
  the #404 DM-consent question does not arise here.
- the reactor is the thread's asker or responder (not a bystander in the channel).
- the thread resolves to an accepted hand-off with extractable Q&A content.

The extraction calls the LLM, which routinely runs past Slack's ~3s event budget,
so :func:`schedule_solve_capture` runs it in a daemon thread and the webhook acks
immediately (mirrors ``notify.schedule_pending_handoff``).
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import sessionmaker

from tekijin.config import Settings, get_settings
from tekijin.data.db import session_scope
from tekijin.data.messages import thread_parties
from tekijin.data.slack_channel_links import get_channel_link_by_channel_id
from tekijin.data.slack_links import get_slack_link_by_slack_user_id
from tekijin.knowledge.extract import CaseExtractor, extract_and_store
from tekijin.knowledge.slack_thread import slack_thread_source

logger = logging.getLogger(__name__)

#: Slack reaction names (no colons) that mean "this thread is solved".
SOLVE_REACTIONS = frozenset({"white_check_mark", "heavy_check_mark", "ok"})


def capture_resolved_thread(
    session_factory: sessionmaker,
    *,
    channel_id: str,
    reactor_slack_user_id: str,
    extractor: CaseExtractor | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Distil the resolved thread behind ``channel_id`` into a knowledge draft.

    Returns the stored source id when a unit was upserted, else ``None`` (any gate
    failed, or the model declined the record as not-a-case). Read-safe and
    idempotent: storage is keyed on the thread's source id, so re-reacting refreshes
    the same draft in place. Errors from extraction/storage are isolated per source
    by :func:`extract_and_store`; this function only opens the transaction.
    """

    settings = settings or get_settings()
    if not settings.slack_solve_capture_enabled:
        return None

    with session_scope(session_factory) as session:
        link = get_channel_link_by_channel_id(session, channel_id)
        if link is None:
            return None  # not a TEKIJIN pair-channel (also excludes every DM)
        thread_id = link.current_thread_id
        if thread_id is None:
            return None
        reactor = get_slack_link_by_slack_user_id(session, reactor_slack_user_id)
        if reactor is None:
            return None
        parties = thread_parties(session, thread_id)
        if parties is None or reactor.employee_id not in (
            parties["asker_id"],
            parties["responder_id"],
        ):
            return None  # a bystander in the channel cannot trigger capture
        source = slack_thread_source(session, thread_id)
        if source is None:
            return None
        extractor = extractor or CaseExtractor(settings=settings)
        counts = extract_and_store(session, [source], extractor)
        return source.source_id if counts["stored"] else None


def schedule_solve_capture(
    session_factory: sessionmaker,
    *,
    channel_id: str,
    reactor_slack_user_id: str,
) -> None:
    """Fire-and-forget :func:`capture_resolved_thread` in a daemon thread.

    The extraction invokes the LLM, which routinely runs past Slack's ~3s event
    budget, so the webhook must ack before this completes. The thread swallows and
    logs its own errors so a failed capture never surfaces as an unhandled-thread
    crash (mirrors ``notify.schedule_pending_handoff``).
    """

    def _run() -> None:
        try:
            stored = capture_resolved_thread(
                session_factory,
                channel_id=channel_id,
                reactor_slack_user_id=reactor_slack_user_id,
            )
            if stored is not None:
                logger.info("Solve-capture stored knowledge draft %s", stored)
        except Exception:  # noqa: BLE001 - background thread boundary, must not crash silently
            logger.warning("Slack solve-capture failed", exc_info=True)

    threading.Thread(target=_run, daemon=True, name="slack-solve-capture").start()
