"""Thin Slack Web API client: OAuth identity exchange + per-thread channel
management + posting.

No Slack SDK dependency — documented REST calls cover this feature's needs:

* ``oauth.v2.access`` — exchange an authorization code for the authorizing
  user's Slack identity ("Sign in with Slack", ``user_scope=identity.basic``).
  Only the returned user/team id is kept; no per-user token is stored, so there
  is nothing to refresh or revoke on Slack's side beyond the identity mapping.
* ``conversations.create`` + ``conversations.invite`` — create the private
  channel for one chat thread (bot + asker + responder) and add the two
  humans (the bot is already a member as the creator).
* ``chat.postMessage`` — post into that channel, authenticated with the app's
  own bot token (never a per-user token).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
_CONVERSATIONS_CREATE_URL = "https://slack.com/api/conversations.create"
_CONVERSATIONS_INVITE_URL = "https://slack.com/api/conversations.invite"
_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_USERS_LIST_URL = "https://slack.com/api/users.list"
# Slack's own maximum is 1000, but it recommends far less; 200 keeps each page
# comfortably inside the Tier-2 rate limit (~20 req/min) for a workspace of
# any size this product plausibly serves.
_USERS_LIST_PAGE_SIZE = 200
# A workspace of 200 * 50 = 10,000 members is far past anything expected here,
# so hitting this means the cursor is not advancing, not that the org is large.
_USERS_LIST_MAX_PAGES = 50
# Identity-only scope: enough to know WHO signed in, not to act as them.
_USER_SCOPE = "identity.basic"
_TIMEOUT_SECONDS = 10.0


class SlackApiError(RuntimeError):
    """Raised when Slack's API responds with ``ok: false`` or an unusable body."""


@dataclass(frozen=True)
class SlackIdentity:
    slack_user_id: str
    slack_team_id: str


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """The "Sign in with Slack" URL to send the browser to."""

    params = {
        "client_id": client_id,
        "user_scope": _USER_SCOPE,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(
    *, client_id: str, client_secret: str, redirect_uri: str, code: str
) -> SlackIdentity:
    """Exchange an OAuth ``code`` for the authorizing user's Slack identity.

    Raises :class:`SlackApiError` on any failure (network, non-2xx, ``ok: false``,
    or a response missing the identity fields) — the caller decides how to
    surface that (a 502 to the browser, in ``slack_routes``).
    """

    resp = httpx.post(
        _OAUTH_ACCESS_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise SlackApiError(body.get("error", "unknown_error"))
    authed_user = body.get("authed_user") or {}
    user_id = authed_user.get("id")
    team_id = (body.get("team") or {}).get("id")
    if not user_id or not team_id:
        raise SlackApiError("missing_identity")
    return SlackIdentity(slack_user_id=user_id, slack_team_id=team_id)


def create_private_channel(*, bot_token: str, name: str) -> str | None:
    """Create a private channel named ``name``; return its id, or ``None`` on
    any failure (logged only — the caller decides whether/how to retry)."""

    headers = {"Authorization": f"Bearer {bot_token}"}
    try:
        resp = httpx.post(
            _CONVERSATIONS_CREATE_URL,
            headers=headers,
            data={"name": name, "is_private": "true"},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise SlackApiError(body.get("error", "unknown_error"))
        return body["channel"]["id"]
    except Exception:
        logger.warning("Slack channel creation failed", exc_info=True)
        return None


def invite_to_channel(*, bot_token: str, channel_id: str, user_ids: list[str]) -> bool:
    """Invite ``user_ids`` into ``channel_id``; return whether it succeeded
    (logged only on failure — the caller decides whether to keep the channel)."""

    headers = {"Authorization": f"Bearer {bot_token}"}
    try:
        resp = httpx.post(
            _CONVERSATIONS_INVITE_URL,
            headers=headers,
            data={"channel": channel_id, "users": ",".join(user_ids)},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise SlackApiError(body.get("error", "unknown_error"))
        return True
    except Exception:
        logger.warning("Slack channel invite failed", exc_info=True)
        return False


def respond_to_response_url(response_url: str, payload: dict) -> None:
    """Best-effort: POST ``payload`` to a Slack interactivity ``response_url``.

    For a Block Kit button click (``block_actions``), Slack does NOT use the
    synchronous HTTP response body of the interactivity request for anything
    except its status code — returning JSON there never changes what the
    message shows. ``response_url`` (included in every interactivity payload,
    valid for a few uses within ~30 minutes) is the documented way to still
    update it: ``{"replace_original": True, ...}`` rewrites the original
    message (e.g. to remove the buttons once handled), while omitting that key
    posts a new message instead — ``{"response_type": "ephemeral", ...}`` for
    one only the clicking user sees (e.g. an authorization rejection that must
    not alter the message the real responder still needs to act on).

    Never raises — this is cosmetic; a failure here must not affect the
    outcome that was already recorded.
    """

    try:
        resp = httpx.post(response_url, json=payload, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except Exception:
        logger.warning("Slack response_url post failed", exc_info=True)


def post_message(
    *, bot_token: str, channel_id: str, text: str, blocks: list[dict] | None = None
) -> None:
    """Best-effort: post ``text`` into ``channel_id`` via the app's bot token.

    Never raises — a Slack outage must not break sending a TEKIJIN chat
    message (callers run this as a background task / thread after the
    message is already saved). Failures are logged only.
    """

    headers = {"Authorization": f"Bearer {bot_token}"}
    try:
        data: dict[str, str] = {"channel": channel_id, "text": text}
        if blocks is not None:
            import json

            data["blocks"] = json.dumps(blocks, ensure_ascii=False)
        resp = httpx.post(
            _POST_MESSAGE_URL,
            headers=headers,
            data=data,
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise SlackApiError(body.get("error", "unknown_error"))
    except Exception:
        logger.warning("Slack channel post failed", exc_info=True)


def list_users(*, bot_token: str) -> list[dict]:
    """Every member of the bot's workspace, following ``users.list`` pagination.

    Raises :class:`SlackApiError` or the underlying HTTP error rather than
    returning a partial list. Callers use this to decide who exists, so "page 1
    of 3" must not be mistaken for the whole workspace — a caller that got a
    short list would simply link fewer people and report success.

    Needs the ``users:read`` scope, plus ``users:read.email`` for the addresses
    the directory join is keyed on (without it Slack omits ``profile.email`` and
    nobody matches).
    """

    headers = {"Authorization": f"Bearer {bot_token}"}
    members: list[dict] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    for _ in range(_USERS_LIST_MAX_PAGES):
        params: dict[str, str | int] = {"limit": _USERS_LIST_PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        resp = httpx.get(_USERS_LIST_URL, headers=headers, params=params, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise SlackApiError(body.get("error", "unknown_error"))
        page = body.get("members")
        if isinstance(page, list):
            members.extend(entry for entry in page if isinstance(entry, dict))
        cursor = ((body.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            return members
        # A cursor that repeats would spin here until the rate limiter bites.
        if cursor in seen_cursors:
            raise SlackApiError("too_many_pages")
        seen_cursors.add(cursor)

    raise SlackApiError("too_many_pages")
