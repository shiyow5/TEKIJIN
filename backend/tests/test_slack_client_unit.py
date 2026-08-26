"""Unit tests for tekijin.slack.client — no real network call: httpx.post is
monkeypatched per test to return a canned response (or raise)."""

from __future__ import annotations

import httpx
import pytest

from tekijin.slack.client import (
    SlackApiError,
    SlackIdentity,
    build_authorize_url,
    create_private_channel,
    exchange_code,
    invite_to_channel,
    post_message,
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


# --- create_private_channel ----------------------------------------------------- #
def test_create_private_channel_success(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: _response(url, 200, {"ok": True, "channel": {"id": "C1"}}),
    )
    assert create_private_channel(bot_token="xoxb-1", name="tekijin-1-2") == "C1"


def test_create_private_channel_returns_none_on_ok_false(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: _response(url, 200, {"ok": False, "error": "name_taken"}),
    )
    assert create_private_channel(bot_token="xoxb-1", name="tekijin-1-2") is None


def test_create_private_channel_returns_none_on_network_error(monkeypatch) -> None:
    def fake_post(url, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", fake_post)
    assert create_private_channel(bot_token="xoxb-1", name="tekijin-1-2") is None


# --- invite_to_channel ------------------------------------------------------------ #
def test_invite_to_channel_success(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url, **kw):
        calls.append(kw.get("data", {}))
        return _response(url, 200, {"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert invite_to_channel(bot_token="xoxb-1", channel_id="C1", user_ids=["U1", "U2"]) is True
    assert calls == [{"channel": "C1", "users": "U1,U2"}]


def test_invite_to_channel_returns_false_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: _response(url, 200, {"ok": False, "error": "already_in_channel"}),
    )
    assert invite_to_channel(bot_token="xoxb-1", channel_id="C1", user_ids=["U1"]) is False


# --- post_message (best-effort: never raises) ----------------------------------- #
def test_post_message_success(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url, **kw):
        calls.append(kw.get("data", {}))
        return _response(url, 200, {"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)
    post_message(bot_token="xoxb-1", channel_id="C1", text="hello")
    assert calls == [{"channel": "C1", "text": "hello"}]


def test_post_message_swallows_ok_false(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: _response(url, 200, {"ok": False, "error": "channel_not_found"}),
    )
    post_message(bot_token="xoxb-1", channel_id="C1", text="hello")  # must not raise


def test_post_message_swallows_network_errors(monkeypatch) -> None:
    def fake_post(url, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", fake_post)
    post_message(bot_token="xoxb-1", channel_id="C1", text="hello")  # must not raise
