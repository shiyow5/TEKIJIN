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

import json
import logging
import threading
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tekijin.config import Settings, get_settings
from tekijin.data.db import session_scope
from tekijin.data.knowledge import get_knowledge_unit_by_source, set_review_status
from tekijin.data.messages import thread_parties
from tekijin.data.slack_channel_links import get_channel_link_by_channel_id
from tekijin.data.slack_links import get_slack_link_by_slack_user_id
from tekijin.data.slack_message_anchors import thread_for_message
from tekijin.knowledge.extract import CaseExtractor, extract_and_store
from tekijin.knowledge.slack_thread import SLACK_THREAD_SOURCE_TYPE, slack_thread_source
from tekijin.slack.client import post_message

logger = logging.getLogger(__name__)

#: Slack reaction names (no colons) that mean "this thread is solved".
SOLVE_REACTIONS = frozenset({"white_check_mark", "heavy_check_mark", "ok"})

#: Interactivity action ids for the in-thread knowledge prompt. keep/discard is the
#: first gate (#476); approve confirms the shown draft (#527 — the review loop runs in
#: Slack, no web hop) and flips it to ``approved`` so retrieval/self-answer trust it.
KNOWLEDGE_KEEP_ACTION = "tekijin_knowledge_keep"
KNOWLEDGE_DISCARD_ACTION = "tekijin_knowledge_discard"
KNOWLEDGE_APPROVE_ACTION = "tekijin_knowledge_approve"
KNOWLEDGE_ACTION_IDS = frozenset(
    {KNOWLEDGE_KEEP_ACTION, KNOWLEDGE_DISCARD_ACTION, KNOWLEDGE_APPROVE_ACTION}
)

#: Cap each draft field shown in Slack so one long extraction can't blow past Slack's
#: 3000-char/section limit or bury the buttons below the fold.
_REVIEW_FIELD_MAX = 500

#: Posted (utterance path) when an explicit "解決" produces no capturable case, so the
#: solver is never met with silence (#525). A plain message — NOT the keep/discard
#: prompt — since there is no draft to keep. The extractor's ``extractable=false`` is a
#: correct guardrail against noise; this only makes its outcome visible and actionable.
_NO_CASE_NOTICE = (
    "「解決」を確認しました。ただ、今回の会話からは知識として残せる具体的な内容"
    "（手順・判断の理由・結論）が見つからなかったため、ナレッジ化は行いませんでした。"
    "要点を書き添えて改めてお知らせいただくと、次回から蓄積できます。"
)

# Threads already given a no-case notice, so repeated solve-utterances on a thread that
# never yields a case don't spam it (#525 review). The success path dedups on its
# persisted draft; the no-case path has no draft to check, and a persisted "declined"
# marker would wrongly block a LATER genuine capture once the thread gains real content —
# so the notice is deduped in-process instead. Reset on restart (a restart may re-notify
# once — acceptable for a UX message). The lock makes claim-then-post atomic so two
# concurrent utterances on the same thread post at most one notice.
_no_case_notified: set[int] = set()
_no_case_notified_lock = threading.Lock()


def _claim_no_case_notice(thread_id: int) -> bool:
    """True the first time a no-case notice is owed for ``thread_id`` this process, else
    False. Gates only the notice — never extraction — so a later utterance that adds real
    content still re-extracts and can succeed via the (independent) draft path."""

    with _no_case_notified_lock:
        if thread_id in _no_case_notified:
            return False
        _no_case_notified.add(thread_id)
        return True


# Conservative solve-utterance markers (substring match). Deliberately RESOLUTION-
# rooted — NOT generic completion/thanks. Excluded on purpose (#519 review): bare
# "できました" (資料ができました / 予約ができました — unrelated completion) and
# "ありがとうございました" (ordinary formal closing). Since dedup consumes the one
# prompt per thread, a false trigger would waste it, so a marker must clearly mean
# "the problem is solved / it works now", not merely "something finished / thanks".
# Product-sensitive: keep tight, and only fires while the feature flag is on.
_SOLVE_UTTERANCES: tuple[str, ...] = (
    "解決しました",
    "解決した",
    "解決です",
    "解決できました",
    "できるようになりました",
    "うまくいきました",
    "直りました",
    "動きました",
)


def is_solve_utterance(text: str) -> bool:
    """True when ``text`` reads as "this is resolved" (a capture trigger, #476)."""
    lowered = text or ""
    return any(marker in lowered for marker in _SOLVE_UTTERANCES)


def _extract_thread_draft(
    session, thread_id: int, extractor: CaseExtractor, *, parties=None
) -> tuple[str | None, dict[str, int] | None]:
    """Distil one resolved thread into a knowledge draft.

    Returns ``(source_id, counts)``: ``source_id`` is the stored draft's id, or ``None``
    when nothing was stored; ``counts`` is ``extract_and_store``'s tally (so a caller can
    tell a genuine "not a case" decision — ``skipped`` — from a transient extraction
    error — ``errored``), or ``None`` when the thread could not even be assembled.

    Shared by the reaction and utterance paths. ``parties`` is threaded through to
    avoid re-running ``thread_parties`` when the caller already has it.
    """

    source = slack_thread_source(session, thread_id, parties=parties)
    if source is None:
        return None, None
    counts = extract_and_store(session, [source], extractor)
    return (source.source_id if counts["stored"] else None), counts


def capture_resolved_thread(
    session_factory: sessionmaker,
    *,
    channel_id: str,
    message_ts: str | None,
    reactor_slack_user_id: str,
    extractor: CaseExtractor | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Distil the reacted-on thread into a knowledge draft.

    ``message_ts`` is the Slack ts of the message the ✅ was placed on. The thread is
    resolved from its recorded per-message anchor (#508) so a reaction on an OLDER
    message on a reused pair channel is attributed to the thread that message
    actually belonged to — not merely the channel's most recent thread. Only when
    the message has no anchor (never mirrored — a bot post, or a message from before
    capture was enabled) does it fall back to ``current_thread_id`` (best-effort).

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
        # #508: attribute the ✅ to the thread the reacted MESSAGE belonged to (its
        # anchor), not the channel's latest thread; fall back to current only when
        # the message has no anchor.
        thread_id = thread_for_message(session, channel_id, message_ts) if message_ts else None
        if thread_id is None:
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
        extractor = extractor or CaseExtractor(settings=settings)
        # Reaction path stores silently (a ✅ is itself the "keep" gesture — no prompt,
        # and no no-case notice); only the stored id matters here.
        stored, _counts = _extract_thread_draft(session, thread_id, extractor, parties=parties)
        return stored


def schedule_solve_capture(
    session_factory: sessionmaker,
    *,
    channel_id: str,
    message_ts: str | None,
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
                message_ts=message_ts,
                channel_id=channel_id,
                reactor_slack_user_id=reactor_slack_user_id,
            )
            if stored is not None:
                logger.info("Solve-capture stored knowledge draft %s", stored)
        except Exception:  # noqa: BLE001 - background thread boundary, must not crash silently
            logger.warning("Slack solve-capture failed", exc_info=True)

    threading.Thread(target=_run, daemon=True, name="slack-solve-capture").start()


def _prompt_blocks(thread_id: int) -> list[dict]:
    """Block Kit for the in-thread "keep / discard" prompt (#476 Screen 02).

    The buttons carry ``thread_id`` in their value, so the interactivity handler
    attributes the click to the exact thread with no ``current_thread_id`` guess
    (the same provenance the anchor gives the reaction path).
    """

    value = json.dumps({"thread_id": thread_id})
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "この会話を *知識として残しますか？* 下書きは用意済みです（確認は30秒ほど）。"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": KNOWLEDGE_KEEP_ACTION,
                    "text": {"type": "plain_text", "text": "残す"},
                    "style": "primary",
                    "value": value,
                },
                {
                    "type": "button",
                    "action_id": KNOWLEDGE_DISCARD_ACTION,
                    "text": {"type": "plain_text", "text": "残さない"},
                    "value": value,
                },
            ],
        },
    ]


def _clip(value: str | None) -> str:
    """A draft field, trimmed to Slack's per-section budget, never blank."""

    text_value = (value or "").strip()
    if not text_value:
        return "（記載なし）"
    if len(text_value) > _REVIEW_FIELD_MAX:
        return text_value[: _REVIEW_FIELD_MAX - 1] + "…"
    return text_value


def review_blocks(
    *,
    thread_id: int,
    problem: str | None,
    action: str | None,
    result: str | None,
    topics: Sequence[str] | None,
    source_id: str,
) -> list[dict]:
    """Block Kit that shows a draft's content in-thread with 承認する / 破棄する (#527).

    This is the "確認" surface: pressing 残す replaces the keep/discard prompt with this,
    so the reviewer reads the extracted case and approves or discards it without leaving
    Slack. Buttons carry ``thread_id`` (same provenance the keep/discard prompt used)."""

    value = json.dumps({"thread_id": thread_id})
    topic_line = "、".join(topics or []) or "（なし）"
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*この下書きを確認してください*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*課題*\n{_clip(problem)}"},
                {"type": "mrkdwn", "text": f"*対処*\n{_clip(action)}"},
                {"type": "mrkdwn", "text": f"*結果*\n{_clip(result)}"},
                {"type": "mrkdwn", "text": f"*トピック*\n{topic_line}"},
            ],
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"出典: {source_id}"}]},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": KNOWLEDGE_APPROVE_ACTION,
                    "text": {"type": "plain_text", "text": "承認する"},
                    "style": "primary",
                    "value": value,
                },
                {
                    "type": "button",
                    "action_id": KNOWLEDGE_DISCARD_ACTION,
                    "text": {"type": "plain_text", "text": "破棄する"},
                    "style": "danger",
                    "value": value,
                },
            ],
        },
    ]


def capture_and_prompt(
    session_factory: sessionmaker,
    *,
    channel_id: str,
    thread_id: int,
    extractor: CaseExtractor | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Utterance path (#476): eagerly draft the resolved thread + post an in-thread
    prompt so the asker/responder can keep or discard it.

    ``thread_id`` is already resolved and its sender party-verified by the caller
    (``_handle_message_event``), so no reactor gate is needed here. Deduped on the
    draft's provenance: if a draft for the thread already exists (a prior utterance
    or a ✅ already captured it), this does nothing — so the prompt is posted at most
    once per thread rather than on every "解決しました". The draft is committed BEFORE
    the prompt is posted, so a fast button click always finds it.

    The "no draft yet → create + prompt" check-then-act is made atomic per thread by
    a transaction-scoped advisory lock (#519 review): two solve-utterances landing in
    the same thread within the extractor's round-trip would otherwise both pass the
    existence check and post two independent prompts. The second caller blocks on the
    lock until the first commits, then sees the draft and returns without posting.

    When the model declines the thread as not-a-case (#525), no draft is created but the
    solver still gets one short notice — never silence — deduped per thread in-process
    (:func:`_claim_no_case_notice`) since there is no persisted draft to dedup on. A
    transient extraction *error* (not a decision) stays silent so the next utterance can
    retry. The flag-off and existing-draft paths remain silent, exactly as before.
    """

    settings = settings or get_settings()
    if not settings.slack_solve_capture_enabled:
        return None

    with session_scope(session_factory) as session:
        # Namespaced (classid=476) so the per-thread lock cannot collide with any
        # other advisory-lock user; released automatically at transaction end.
        session.execute(
            text("SELECT pg_advisory_xact_lock(:cls, :obj)"), {"cls": 476, "obj": thread_id}
        )
        existing = get_knowledge_unit_by_source(
            session, SLACK_THREAD_SOURCE_TYPE, f"slack_thread_{thread_id}"
        )
        if existing is not None:
            return None  # already captured + prompted for this thread (stay silent)
        extractor = extractor or CaseExtractor(settings=settings)
        stored, counts = _extract_thread_draft(session, thread_id, extractor)

    # Best-effort posts (never raise): a lost post is a lost message, not lost data.
    if stored is None:
        # The model declined the thread as not-a-case (#525): an explicit "解決" must
        # not be met with silence, so tell the solver why nothing was saved. A plain
        # notice — there is no draft to keep, so no keep/discard prompt. Only for a
        # genuine decision (skipped), at most once per thread; a transient error
        # (errored) or an unassemblable thread (counts is None) stays silent so the
        # next utterance retries rather than showing a wrong "見つからなかった" reason.
        if counts is not None and counts.get("skipped") and _claim_no_case_notice(thread_id):
            post_message(
                bot_token=settings.slack_bot_token,
                channel_id=channel_id,
                text=_NO_CASE_NOTICE,
            )
        return None

    # A draft exists (committed above, so a fast button click always finds it): offer
    # the in-thread keep/discard prompt for the management review box (#477).
    post_message(
        bot_token=settings.slack_bot_token,
        channel_id=channel_id,
        text="この会話を知識として残しますか？",
        blocks=_prompt_blocks(thread_id),
    )
    return stored


def schedule_solve_prompt(
    session_factory: sessionmaker,
    *,
    channel_id: str,
    thread_id: int,
) -> None:
    """Fire-and-forget :func:`capture_and_prompt` in a daemon thread (LLM + Slack
    post both exceed Slack's ~3s event budget)."""

    def _run() -> None:
        try:
            stored = capture_and_prompt(session_factory, channel_id=channel_id, thread_id=thread_id)
            if stored is not None:
                logger.info("Solve-utterance prompted for knowledge draft %s", stored)
        except Exception:  # noqa: BLE001 - background thread boundary, must not crash silently
            logger.warning("Slack solve-prompt failed", exc_info=True)

    threading.Thread(target=_run, daemon=True, name="slack-solve-prompt").start()


def discard_thread_draft(session, thread_id: int) -> bool:
    """Mark a thread's knowledge draft rejected ("残さない"). Returns whether one was
    found. Rejecting (not deleting) is durable: a later re-capture upserts content
    but never revives ``review_status``, so a discarded thread stays discarded."""

    unit = get_knowledge_unit_by_source(
        session, SLACK_THREAD_SOURCE_TYPE, f"slack_thread_{thread_id}"
    )
    if unit is None:
        return False
    set_review_status(session, unit.id, "rejected")
    return True
