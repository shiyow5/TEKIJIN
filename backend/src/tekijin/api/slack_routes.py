"""Slack account linking + events: GET /slack/authorize-url,
GET /slack/oauth/callback, GET /slack/status, POST /slack/unlink,
POST /slack/events.

"Sign in with Slack" account linking, independent of whether DM notifications
are turned on (``Settings.slack_notifications_enabled``) — an employee can link
before a bot token exists; notifications simply stay off until one does. The
OAuth ``state`` param is a short-lived signed JWT carrying the employee id
(:func:`_encode_state` / :func:`_decode_state`) rather than a server-side
session, matching this API's existing stateless-bearer-token design: the
callback is a plain browser redirect from Slack with no ``Authorization``
header available, so the state itself has to prove which employee is linking.

``POST /slack/events`` is the Slack -> TEKIJIN direction (#388, #hand-off-chat):
Slack's Events API calls this directly (no TEKIJIN principal — it
authenticates itself via a request signature instead,
:func:`tekijin.slack.verify.verify_signature`). A message posted in a
hand-off's shared Slack channel is routed to whichever TEKIJIN thread that
channel's ``current_thread_id`` currently names (see
``tekijin.slack.notify``'s module docstring) — no relay back into Slack is
needed here, since both parties already see each other's messages natively
in the same channel; this only mirrors them into TEKIJIN.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from urllib.parse import parse_qs

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from tekijin.api import schemas
from tekijin.api.service import AgentService
from tekijin.auth.deps import require_principal
from tekijin.auth.principal import Principal
from tekijin.config import get_settings
from tekijin.data.db import session_scope
from tekijin.data.messages import create_message, thread_parties
from tekijin.data.slack_channel_links import get_channel_link_by_channel_id
from tekijin.data.slack_links import (
    delete_slack_link,
    get_slack_link,
    get_slack_link_by_slack_user_id,
    upsert_slack_link,
)
from tekijin.slack.client import build_authorize_url, exchange_code, respond_to_response_url
from tekijin.slack.verify import verify_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])

_STATE_PURPOSE = "slack_link"
_STATE_TTL_MINUTES = 10.0


def _require_linkable_employee(principal: Principal) -> int:
    """An admin has no employee id and never receives chat messages — nothing
    for them to link."""

    if principal.employee_id is None:
        raise HTTPException(status_code=403, detail="管理者はSlack連携を利用できません。")
    return principal.employee_id


def _encode_state(employee_id: int, *, secret: str) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "purpose": _STATE_PURPOSE,
        "employee_id": employee_id,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=_STATE_TTL_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _decode_state(state: str, *, secret: str) -> int:
    try:
        payload = jwt.decode(state, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail="invalid or expired state") from exc
    if payload.get("purpose") != _STATE_PURPOSE:
        raise HTTPException(status_code=400, detail="invalid state")
    return int(payload["employee_id"])


@router.get("/authorize-url", response_model=schemas.SlackAuthorizeUrlResponse)
def authorize_url(
    principal: Principal = Depends(require_principal),
) -> schemas.SlackAuthorizeUrlResponse:
    """The "Sign in with Slack" URL for the frontend to navigate to.

    503 (not a client error) while no Slack App is registered yet
    (``TEKIJIN_SLACK_CLIENT_ID`` / ``_SECRET`` / ``_REDIRECT_URI`` unset) — the
    button is a temporarily-unavailable feature, not a caller mistake.
    """

    employee_id = _require_linkable_employee(principal)
    settings = get_settings()
    if not settings.slack_configured():
        raise HTTPException(status_code=503, detail="Slack連携は現在利用できません。")
    state = _encode_state(employee_id, secret=settings.auth_secret)
    url = build_authorize_url(
        client_id=settings.slack_client_id,
        redirect_uri=settings.slack_redirect_uri,
        state=state,
    )
    return schemas.SlackAuthorizeUrlResponse(url=url)


@router.get("/oauth/callback")
def oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Slack's OAuth redirect target. No ``Authorization`` header is available
    here (this is a top-level browser navigation, not a fetch from the SPA), so
    the employee comes from the signed ``state`` instead (#slack-integration).

    Always ends in a redirect back to the frontend chat page — success or
    failure — never a bare JSON error, since a human is looking at the browser
    at this point, not calling the API.
    """

    settings = get_settings()
    frontend_chat_url = f"{settings.slack_frontend_url.rstrip('/')}/chat"
    if error or not code or not state:
        return RedirectResponse(f"{frontend_chat_url}?slack=error")
    try:
        employee_id = _decode_state(state, secret=settings.auth_secret)
        identity = exchange_code(
            client_id=settings.slack_client_id,
            client_secret=settings.slack_client_secret,
            redirect_uri=settings.slack_redirect_uri,
            code=code,
        )
        service = request.app.state.agent_service
        with session_scope(service.session_factory) as session:
            # Also covers the DB write: `slack_user_id` is unique, so a Slack
            # account already linked to a DIFFERENT employee raises an
            # IntegrityError here — that must redirect to ?slack=error like any
            # other OAuth failure, not surface as a bare 500 (the docstring
            # above promises this callback ALWAYS redirects).
            upsert_slack_link(
                session,
                employee_id,
                slack_user_id=identity.slack_user_id,
                slack_team_id=identity.slack_team_id,
                now=dt.datetime.now(),  # noqa: DTZ005 - naive is intentional, matches created_at elsewhere
            )
    except Exception:  # noqa: BLE001 - browser redirect boundary, never surfaces a bare 4xx/5xx
        logger.warning("Slack OAuth callback failed", exc_info=True)
        return RedirectResponse(f"{frontend_chat_url}?slack=error")
    return RedirectResponse(f"{frontend_chat_url}?slack=linked")


@router.get("/status", response_model=schemas.SlackStatusResponse)
def status(
    request: Request,
    principal: Principal = Depends(require_principal),
) -> schemas.SlackStatusResponse:
    if principal.employee_id is None:
        return schemas.SlackStatusResponse(linked=False)
    service = request.app.state.agent_service
    with service.session_factory() as session:
        link = get_slack_link(session, principal.employee_id)
    return schemas.SlackStatusResponse(linked=link is not None)


@router.post("/unlink", response_model=schemas.SlackUnlinkResponse)
def unlink(
    request: Request,
    principal: Principal = Depends(require_principal),
) -> schemas.SlackUnlinkResponse:
    employee_id = _require_linkable_employee(principal)
    service = request.app.state.agent_service
    with session_scope(service.session_factory) as session:
        delete_slack_link(session, employee_id)
    return schemas.SlackUnlinkResponse()


def _handle_message_event(session_factory: sessionmaker[Session], event: dict) -> None:
    """Mirror one message posted in a hand-off's shared Slack channel into
    the TEKIJIN thread it currently belongs to (#hand-off-chat).

    Silently drops anything it cannot confidently route (channel isn't one of
    ours, unknown sender, sender no longer a party) rather than erroring —
    this runs inside a webhook Slack will retry on any non-2xx, so "nothing
    to do" must look identical to "handled" from Slack's side. Synchronous
    (plain ``def``, run via ``run_in_threadpool`` by the caller): does
    blocking DB I/O, which must never run directly on the asyncio event loop
    that ``POST /slack/events`` (an ``async def`` handler) shares with every
    other concurrent request this worker is serving.
    """

    if event.get("type") != "message":
        return
    # Never the bot's own post landing back in the same channel it was sent
    # to (that would loop), and message_changed / message_deleted / channel
    # joins etc. are not a new human message.
    if event.get("bot_id") or event.get("subtype"):
        return
    channel_id = event.get("channel")
    slack_user_id = event.get("user")
    text = event.get("text")
    if not channel_id or not slack_user_id or not text or not text.strip():
        return

    with session_scope(session_factory) as session:
        channel_link = get_channel_link_by_channel_id(session, channel_id)
        if channel_link is None:
            return  # not a channel TEKIJIN created — ignore
        sender_link = get_slack_link_by_slack_user_id(session, slack_user_id)
        if sender_link is None:
            return
        thread_id = channel_link.current_thread_id
        sender_id = sender_link.employee_id
        parties = thread_parties(session, thread_id)
        if parties is None or sender_id not in (parties["asker_id"], parties["responder_id"]):
            return
        now = dt.datetime.now()  # noqa: DTZ005 - naive is intentional, matches created_at elsewhere
        create_message(session, thread_id, sender_id, text, now)


@router.post("/events")
async def events(request: Request) -> Response:
    """Slack Events API endpoint: the URL-verification handshake, plus inbound
    hand-off-channel messages mirrored into the matching TEKIJIN chat thread
    (#hand-off-chat).

    No TEKIJIN auth — Slack calls this directly, authenticated instead by its
    own request signature. Always acks within Slack's 3s budget and never
    raises past a signature failure: anything else it can't route is just a
    no-op (see :func:`_handle_message_event`), so a malformed or duplicate
    delivery never turns into a 5xx that trains Slack to keep retrying.

    ``event_id`` de-dup guards against Slack's OWN retries (it re-delivers
    whenever an earlier attempt didn't ack in time — plausible before the
    event-loop fix above, but Slack does not guarantee exactly-once even
    otherwise) so a retried delivery can never insert the same message twice.
    """

    settings = get_settings()
    body = await request.body()
    if not verify_signature(
        signing_secret=settings.slack_signing_secret,
        timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
        signature=request.headers.get("X-Slack-Signature", ""),
        body=body,
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = json.loads(body)
    if payload.get("type") == "url_verification":
        # Slack's documented handshake: echo the challenge back as plain text.
        return PlainTextResponse(str(payload.get("challenge", "")))

    if payload.get("type") == "event_callback":
        event_id = payload.get("event_id")
        seen_events = request.app.state.slack_seen_event_ids
        if event_id is None or not seen_events.seen_before(event_id):
            event = payload.get("event") or {}
            service = request.app.state.agent_service
            await run_in_threadpool(_handle_message_event, service.session_factory, event)

    return JSONResponse({"ok": True})


_INTERACTIVITY_FAILURE_TEXT = "この依頼は処理できませんでした。TEKIJINで状態を確認してください。"

_RESULT_TEXT_BY_ACTION_ID = {
    "tekijin_accept": "承諾しました。TEKIJINの依頼状態を更新しています。",
    "tekijin_decline": "辞退しました。依頼者へ通知します。",
    "tekijin_refer": "「自分より適任がいる」として辞退しました。依頼者へ通知します。",
}


def _advance_after_resume(service: AgentService, session_id: str) -> None:
    """Drain the just-queued resume so the paused run actually advances.

    ``submit_resume`` only queues a ``Command`` (service.py); the frontend's
    own button click always follows it with ``advanceSession()`` ->
    ``GET /events/{session_id}`` to consume that queue and drive the graph
    forward (accept -> done, decline -> reroute to the next candidate) — see
    that function's doc comment. A Slack click has no such stream of its own,
    so without this the hand-off would sit parked until the asker's tab
    happens to reconnect. Runs fire-and-forget in its own thread (mirrors
    ``notify.schedule_pending_handoff``): draining can invoke LLM calls (e.g.
    the reroute), which routinely runs well past Slack's ~3s interactivity
    budget.
    """

    try:
        if service.is_streamable(session_id):
            for _ in service.stream_events(session_id):
                pass
    except Exception:
        logger.warning(
            "Slack interactivity: failed to advance session %s", session_id, exc_info=True
        )


def _handle_interactivity_action(service: AgentService, raw: str) -> Response:
    """Apply one Slack 承諾/辞退/自分より適任がいる button click.

    Synchronous — does blocking DB I/O and can hold ``submit_resume``'s
    per-session lock — so the caller runs it via ``run_in_threadpool`` rather
    than inline on the event loop (matching ``_handle_message_event`` above).

    Never lets an exception (including an auth mismatch) escape as a raw
    non-2xx: that is exactly what makes Slack mark the message with its
    "processing failed" warning triangle, so every failure here instead
    resolves to a 200 with a friendly Slack-facing message.

    For a ``block_actions`` payload (a button click), that 200 JSON body is
    otherwise inert — Slack only looks at its status code, so returning
    ``{"text": ...}`` here does NOT update the message or remove the buttons.
    ``response_url`` (below) is what actually changes what the message shows.
    """

    response_url: str | None = None
    original_blocks: list[dict] = []
    try:
        payload = json.loads(raw)
        response_url = payload.get("response_url")
        # The message this action was attached to (Slack echoes it back in the
        # payload) — kept so a successful click can drop just the button row
        # instead of wiping the whole message, including the consultation
        # text, when it replaces the message below.
        original_blocks = (payload.get("message") or {}).get("blocks") or []
        action = (payload.get("actions") or [])[0]
        action_id = action.get("action_id")
        value = json.loads(action.get("value", "{}"))
        session_id = value["session_id"]
        outcome = value["outcome"]
        recommendation_id = int(value["recommendation_id"])
        slack_user_id = (payload.get("user") or {}).get("id")
        with service.session_factory() as session:
            link = get_slack_link_by_slack_user_id(session, slack_user_id)
            responder_id = link.employee_id if link else None
        _, current_responder_id = service.session_participants(session_id)
        if responder_id is None or responder_id != current_responder_id:
            logger.info(
                "Slack interactivity: slack_user %s is not session %s's assigned responder",
                slack_user_id,
                session_id,
            )
            # Ephemeral (visible only to the clicker), NOT replace_original: the
            # real responder still needs the buttons intact on this message.
            if response_url:
                respond_to_response_url(
                    response_url,
                    {"response_type": "ephemeral", "text": "この操作を行う権限がありません。"},
                )
            return JSONResponse({"text": "この操作を行う権限がありません。"})
        service.submit_resume(session_id, outcome=outcome, recommendation_id=recommendation_id)
    except Exception:
        logger.warning("Slack interactivity handling failed", exc_info=True)
        if response_url:
            respond_to_response_url(
                response_url, {"response_type": "ephemeral", "text": _INTERACTIVITY_FAILURE_TEXT}
            )
        return JSONResponse({"text": _INTERACTIVITY_FAILURE_TEXT})

    threading.Thread(
        target=_advance_after_resume,
        args=(service, session_id),
        daemon=True,
        name="slack-interactivity-advance",
    ).start()
    text = _RESULT_TEXT_BY_ACTION_ID.get(
        action_id,
        "承諾しました。" if outcome == "accepted" else "辞退しました。依頼者へ通知します。",
    )
    # Handled -> rewrite the message dropping only the button row (so it can't
    # be clicked, or double-clicked, again) while keeping the original
    # consultation text intact, with the outcome appended as its own line.
    if response_url:
        kept_blocks = [b for b in original_blocks if b.get("type") != "actions"]
        kept_blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{text}*"}})
        respond_to_response_url(
            response_url,
            {"replace_original": True, "text": text, "blocks": kept_blocks},
        )
    return JSONResponse({"text": text})


@router.post("/interactivity")
async def interactivity(request: Request) -> Response:
    """Handle Slack Block Kitの承諾・辞退・自分より適任がいるボタン for pending hand-offs."""
    settings = get_settings()
    body = await request.body()
    if not verify_signature(
        signing_secret=settings.slack_signing_secret,
        timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
        signature=request.headers.get("X-Slack-Signature", ""),
        body=body,
    ):
        raise HTTPException(status_code=401, detail="invalid signature")
    raw = parse_qs(body.decode("utf-8"), keep_blank_values=True).get("payload", [""])[0]
    service = request.app.state.agent_service
    return await run_in_threadpool(_handle_interactivity_action, service, raw)
