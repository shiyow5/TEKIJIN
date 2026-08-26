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

``POST /slack/events`` is the Slack -> TEKIJIN direction (#388): Slack's Events
API calls this directly (no TEKIJIN principal — it authenticates itself via a
request signature instead, :func:`tekijin.slack.verify.verify_signature`), and
a DM reply is routed into whichever thread that employee was last notified
about (see ``SlackLink.last_notified_thread_id``).
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from tekijin.api import schemas
from tekijin.auth.deps import require_principal
from tekijin.auth.principal import Principal
from tekijin.config import get_settings
from tekijin.data.db import session_scope
from tekijin.data.messages import create_message, thread_parties
from tekijin.data.slack_links import (
    delete_slack_link,
    get_slack_link,
    get_slack_link_by_slack_user_id,
    upsert_slack_link,
)
from tekijin.slack.client import build_authorize_url, exchange_code
from tekijin.slack.notify import maybe_notify_via_slack
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
    except Exception:  # noqa: BLE001 - browser redirect boundary, never surfaces a bare 4xx/5xx
        logger.warning("Slack OAuth callback failed", exc_info=True)
        return RedirectResponse(f"{frontend_chat_url}?slack=error")

    service = request.app.state.agent_service
    with session_scope(service.session_factory) as session:
        upsert_slack_link(
            session,
            employee_id,
            slack_user_id=identity.slack_user_id,
            slack_team_id=identity.slack_team_id,
            now=dt.datetime.now(),  # noqa: DTZ005 - naive is intentional, matches created_at elsewhere
        )
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


def _handle_message_event(
    session_factory: sessionmaker[Session], background_tasks: BackgroundTasks, event: dict
) -> None:
    """Route one inbound Slack DM into the matching TEKIJIN thread (#388).

    Silently drops anything it cannot confidently route (unknown sender, no
    remembered thread, sender no longer a party) rather than erroring — this
    runs inside a webhook Slack will retry on any non-2xx, so "nothing to do"
    must look identical to "handled" from Slack's side.
    """

    if event.get("type") != "message":
        return
    # Only DMs to the bot (not channel/group messages some other subscribed
    # scope might deliver), and never the bot's own notification landing back
    # in the same DM channel it was posted to (that would loop).
    if event.get("channel_type") != "im" or event.get("bot_id"):
        return
    # message_changed / message_deleted / channel_join etc. — not a new reply.
    if event.get("subtype"):
        return
    slack_user_id = event.get("user")
    text = event.get("text")
    if not slack_user_id or not text or not text.strip():
        return

    with session_scope(session_factory) as session:
        link = get_slack_link_by_slack_user_id(session, slack_user_id)
        if link is None or link.last_notified_thread_id is None:
            return
        thread_id = link.last_notified_thread_id
        sender_id = link.employee_id
        parties = thread_parties(session, thread_id)
        if parties is None or sender_id not in (parties["asker_id"], parties["responder_id"]):
            return
        now = dt.datetime.now()  # noqa: DTZ005 - naive is intentional, matches created_at elsewhere
        create_message(session, thread_id, sender_id, text, now)
        maybe_notify_via_slack(
            session,
            background_tasks,
            parties=parties,
            sender_id=sender_id,
            body=text,
            thread_id=thread_id,
        )


@router.post("/events")
async def events(request: Request, background_tasks: BackgroundTasks) -> Response:
    """Slack Events API endpoint: the URL-verification handshake, plus inbound
    DM replies relayed into the matching TEKIJIN chat thread (#388).

    No TEKIJIN auth — Slack calls this directly, authenticated instead by its
    own request signature. Always acks within Slack's 3s budget and never
    raises past a signature failure: anything else it can't route is just a
    no-op (see :func:`_handle_message_event`), so a malformed or duplicate
    delivery never turns into a 5xx that trains Slack to keep retrying.
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
        event = payload.get("event") or {}
        service = request.app.state.agent_service
        _handle_message_event(service.session_factory, background_tasks, event)

    return JSONResponse({"ok": True})
