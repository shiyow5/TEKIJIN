"""Unit tests for tekijin.slack.client — no real network call: httpx.post is
monkeypatched per test to return a canned response (or raise)."""

from __future__ import annotations

import httpx
import pytest

from tekijin.slack.client import (
    SlackApiError,
    SlackIdentity,
    build_authorize_url,
    exchange_code,
    send_dm,
)


def _response(url: str, status_code: int, json_body: dict) -> httpx.Response:
    return httpx.Response(status_code, json=json_body, request=httpx.Request("POST", url))


# --- build_authorize_url ------------------------------------------------------ #
def test_build_authorize_url_includes_all_params() -> None:
    url = build_authorize_url(
        client_id="cid", redirect_uri="http://localhost:8000/slack/oauth/callback", state="st"
    )
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=cid" in url
    assert "user_scope=identity.basic" in url
    assert "state=st" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fslack%2Foauth%2Fcallback" in url


# --- exchange_code -------------------------------------------------------------- #
def test_exchange_code_success(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: _response(
            url, 200, {"ok": True, "authed_user": {"id": "U1"}, "team": {"id": "T1"}}
        ),
    )
    identity = exchange_code(
        client_id="cid", client_secret="secret", redirect_uri="http://x", code="code123"
    )
    assert identity == SlackIdentity(slack_user_id="U1", slack_team_id="T1")


def test_exchange_code_raises_on_ok_false(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: _response(url, 200, {"ok": False, "error": "invalid_code"}),
    )
    with pytest.raises(SlackApiError, match="invalid_code"):
        exchange_code(client_id="cid", client_secret="secret", redirect_uri="http://x", code="c")


def test_exchange_code_raises_on_missing_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: _response(url, 200, {"ok": True, "team": {"id": "T1"}}),
    )
    with pytest.raises(SlackApiError, match="missing_identity"):
        exchange_code(client_id="cid", client_secret="secret", redirect_uri="http://x", code="c")


def test_exchange_code_propagates_http_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: _response(url, 500, {}),
    )
    with pytest.raises(httpx.HTTPStatusError):
        exchange_code(client_id="cid", client_secret="secret", redirect_uri="http://x", code="c")


# --- send_dm (best-effort: never raises) --------------------------------------- #
def test_send_dm_success_opens_conversation_then_posts(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_post(url, **kw):
        calls.append((url, kw.get("data", {})))
        if url.endswith("conversations.open"):
            return _response(url, 200, {"ok": True, "channel": {"id": "D1"}})
        return _response(url, 200, {"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)
    send_dm(bot_token="xoxb-1", slack_user_id="U1", text="hello")

    assert [url for url, _ in calls] == [
        "https://slack.com/api/conversations.open",
        "https://slack.com/api/chat.postMessage",
    ]
    assert calls[0][1] == {"users": "U1"}
    assert calls[1][1] == {"channel": "D1", "text": "hello"}


def test_send_dm_swallows_conversations_open_failure(monkeypatch) -> None:
    calls: list[str] = []

    def fake_post(url, **kw):
        calls.append(url)
        return _response(url, 200, {"ok": False, "error": "user_not_found"})

    monkeypatch.setattr(httpx, "post", fake_post)
    send_dm(bot_token="xoxb-1", slack_user_id="U1", text="hello")  # must not raise

    assert calls == ["https://slack.com/api/conversations.open"]  # postMessage never reached


def test_send_dm_swallows_post_message_failure(monkeypatch) -> None:
    def fake_post(url, **kw):
        if url.endswith("conversations.open"):
            return _response(url, 200, {"ok": True, "channel": {"id": "D1"}})
        return _response(url, 200, {"ok": False, "error": "channel_not_found"})

    monkeypatch.setattr(httpx, "post", fake_post)
    send_dm(bot_token="xoxb-1", slack_user_id="U1", text="hello")  # must not raise


def test_send_dm_swallows_network_errors(monkeypatch) -> None:
    def fake_post(url, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", fake_post)
    send_dm(bot_token="xoxb-1", slack_user_id="U1", text="hello")  # must not raise
