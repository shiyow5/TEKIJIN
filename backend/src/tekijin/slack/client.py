"""Thin Slack Web API client: OAuth identity exchange + DM notification.

No Slack SDK dependency — two documented REST calls cover this feature's needs:

* ``oauth.v2.access`` — exchange an authorization code for the authorizing
  user's Slack identity ("Sign in with Slack", ``user_scope=identity.basic``).
  Only the returned user/team id is kept; no per-user token is stored, so there
  is nothing to refresh or revoke on Slack's side beyond the identity mapping.
* ``conversations.open`` + ``chat.postMessage`` — open a DM with a linked user
  and post a notification, authenticated with the app's own bot token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
_CONVERSATIONS_OPEN_URL = "https://slack.com/api/conversations.open"
_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
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


def send_dm(*, bot_token: str, slack_user_id: str, text: str) -> None:
    """Best-effort: DM ``text`` to ``slack_user_id`` via the app's bot token.

    Never raises — a Slack outage or a revoked/invalid link must not break
    sending a TEKIJIN chat message (the caller runs this as a background task
    after the message is already saved). Failures are logged only.
    """

    headers = {"Authorization": f"Bearer {bot_token}"}
    try:
        opened = httpx.post(
            _CONVERSATIONS_OPEN_URL,
            headers=headers,
            data={"users": slack_user_id},
            timeout=_TIMEOUT_SECONDS,
        )
        opened.raise_for_status()
        channel_body = opened.json()
        if not channel_body.get("ok"):
            raise SlackApiError(channel_body.get("error", "unknown_error"))
        channel_id = channel_body["channel"]["id"]

        posted = httpx.post(
            _POST_MESSAGE_URL,
            headers=headers,
            data={"channel": channel_id, "text": text},
            timeout=_TIMEOUT_SECONDS,
        )
        posted.raise_for_status()
        post_body = posted.json()
        if not post_body.get("ok"):
            raise SlackApiError(post_body.get("error", "unknown_error"))
    except Exception:
        logger.warning("Slack DM notification failed", exc_info=True)
