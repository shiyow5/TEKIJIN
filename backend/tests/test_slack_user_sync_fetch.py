"""``users.list`` is paginated and can fail halfway. Both matter here.

A partial member list is dangerous in a way a partial list usually isn't: the
planner treats "present and deleted" as a departure signal, so a fetch that
silently returns page 1 of 3 would look like a smaller workspace. It doesn't
cause spurious unlinks (absence never unlinks — see the planner), but it does
cause silently incomplete linking, so a failed page must raise rather than
return what it has.
"""

from __future__ import annotations

import pytest

from tekijin.slack import client as slack_client


class _Resp:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body = body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


def test_list_users_follows_every_cursor(monkeypatch) -> None:
    pages = [
        {
            "ok": True,
            "members": [{"id": "U1"}],
            "response_metadata": {"next_cursor": "CUR2"},
        },
        {
            "ok": True,
            "members": [{"id": "U2"}],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    seen_cursors = []

    def fake_get(url, **kwargs):
        seen_cursors.append(kwargs["params"].get("cursor"))
        return _Resp(pages[len(seen_cursors) - 1])

    monkeypatch.setattr(slack_client.httpx, "get", fake_get)

    members = slack_client.list_users(bot_token="xoxb-t")

    assert [m["id"] for m in members] == ["U1", "U2"]
    assert seen_cursors == [None, "CUR2"]


def test_list_users_raises_when_slack_says_not_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        slack_client.httpx,
        "get",
        lambda url, **kw: _Resp({"ok": False, "error": "missing_scope"}),
    )

    with pytest.raises(slack_client.SlackApiError, match="missing_scope"):
        slack_client.list_users(bot_token="xoxb-t")


def test_list_users_raises_instead_of_returning_a_half_read_workspace(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(
                {
                    "ok": True,
                    "members": [{"id": "U1"}],
                    "response_metadata": {"next_cursor": "CUR2"},
                }
            )
        return _Resp({}, status=500)

    monkeypatch.setattr(slack_client.httpx, "get", fake_get)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        slack_client.list_users(bot_token="xoxb-t")


def test_list_users_stops_rather_than_looping_forever(monkeypatch) -> None:
    """A cursor that keeps pointing at itself would otherwise pin a worker
    thread and hammer Slack until the rate limiter cuts in."""

    monkeypatch.setattr(
        slack_client.httpx,
        "get",
        lambda url, **kw: _Resp(
            {
                "ok": True,
                "members": [{"id": "U1"}],
                "response_metadata": {"next_cursor": "SAME"},
            }
        ),
    )

    with pytest.raises(slack_client.SlackApiError, match="too_many_pages"):
        slack_client.list_users(bot_token="xoxb-t")
