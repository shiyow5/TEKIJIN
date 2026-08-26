"""Integration tests for Slack account linking (GET /slack/authorize-url,
GET /slack/oauth/callback, GET /slack/status, POST /slack/unlink).

Talks to the live seeded DB (pgserver/CI), same as ``test_auth_integration.py``.
No real Slack API calls: ``exchange_code`` is monkeypatched at the module
boundary it's imported into.
"""

from __future__ import annotations

import datetime as dt

import jwt
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from tekijin.agent.stubs import KeywordIntentModel, RuleSufficiencyModel, TemplateDraftModel
from tekijin.api.service import AgentService
from tekijin.app import create_app
from tekijin.auth.principal import Principal
from tekijin.auth.tokens import create_access_token
from tekijin.config import get_settings
from tekijin.data.db import get_sessionmaker
from tekijin.data.slack_links import get_slack_link, upsert_slack_link
from tekijin.slack.client import SlackIdentity

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)


def _admin_headers() -> dict[str, str]:
    token = create_access_token(
        Principal(employee_id=None, name="管理者", dept=None, is_admin=True),
        secret=get_settings().auth_secret,
        ttl_hours=1,
    )
    return {"Authorization": f"Bearer {token}"}


def _user_headers(employee_id: int) -> dict[str, str]:
    token = create_access_token(
        Principal(employee_id=employee_id, name="社員", dept=None, is_admin=False),
        secret=get_settings().auth_secret,
        ttl_hours=1,
    )
    return {"Authorization": f"Bearer {token}"}


def _raw_client(engine, embedder) -> TestClient:
    service = AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=MemorySaver(),
        embedder=embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=TemplateDraftModel(),
    )
    return TestClient(create_app(agent_service=service))


@pytest.fixture
def slack_app_configured(monkeypatch):
    """Set a full (fake) Slack App config for the duration of one test."""

    monkeypatch.setenv("TEKIJIN_SLACK_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("TEKIJIN_SLACK_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("TEKIJIN_SLACK_REDIRECT_URI", "http://localhost:8000/slack/oauth/callback")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def slack_app_unconfigured(monkeypatch):
    """Ensure no Slack App config leaks in from the environment."""

    for key in (
        "TEKIJIN_SLACK_CLIENT_ID",
        "TEKIJIN_SLACK_CLIENT_SECRET",
        "TEKIJIN_SLACK_REDIRECT_URI",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


# --- GET /slack/authorize-url ------------------------------------------------ #
def test_authorize_url_503_when_not_configured(
    slack_app_unconfigured, seed_counts, engine, fake_embedder
) -> None:
    client = _raw_client(engine, fake_embedder)
    resp = client.get("/slack/authorize-url", headers=_user_headers(5))
    assert resp.status_code == 503


def test_authorize_url_state_names_the_purpose_but_not_the_employee(
    slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    client = _raw_client(engine, fake_embedder)
    resp = client.get("/slack/authorize-url", headers=_user_headers(5))
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=test-client-id" in url

    state = dict(pair.split("=", 1) for pair in url.split("?", 1)[1].split("&"))["state"]
    payload = jwt.decode(state, get_settings().auth_secret, algorithms=["HS256"])
    assert payload["purpose"] == "slack_link"
    # Deliberately absent (#494): if the state named the employee, forwarding
    # this URL to someone else would attach THEIR Slack account to THIS caller.
    assert "employee_id" not in payload


def test_authorize_url_forbidden_for_admin(
    slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    client = _raw_client(engine, fake_embedder)
    resp = client.get("/slack/authorize-url", headers=_admin_headers())
    assert resp.status_code == 403


# --- GET /slack/status, POST /slack/unlink ----------------------------------- #
def test_status_and_unlink_roundtrip(seed_counts, engine, fake_embedder) -> None:
    with get_sessionmaker(engine)() as session:
        upsert_slack_link(session, 6, slack_user_id="U_SIX", slack_team_id="T1", now=NOW)
        session.commit()

    client = _raw_client(engine, fake_embedder)
    status = client.get("/slack/status", headers=_user_headers(6)).json()
    assert status["linked"] is True

    unlink_resp = client.post("/slack/unlink", headers=_user_headers(6))
    assert unlink_resp.status_code == 200
    assert unlink_resp.json()["ok"] is True

    assert client.get("/slack/status", headers=_user_headers(6)).json()["linked"] is False


def test_status_false_for_an_unlinked_employee(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    assert client.get("/slack/status", headers=_user_headers(7)).json()["linked"] is False


def test_status_always_false_for_admin(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    assert client.get("/slack/status", headers=_admin_headers()).json()["linked"] is False


def test_unlink_forbidden_for_admin(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    assert client.post("/slack/unlink", headers=_admin_headers()).status_code == 403


# --- GET /slack/oauth/callback ------------------------------------------------ #
def test_oauth_callback_missing_params_redirects_to_error(
    seed_counts, engine, fake_embedder
) -> None:
    client = _raw_client(engine, fake_embedder)
    resp = client.get("/slack/oauth/callback", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "slack=error" in resp.headers["location"]


def test_oauth_callback_invalid_state_redirects_to_error(
    slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    client = _raw_client(engine, fake_embedder)
    resp = client.get(
        "/slack/oauth/callback",
        params={"code": "abc", "state": "not-a-real-token"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "slack=error" in resp.headers["location"]


def test_oauth_callback_success_links_and_redirects(
    monkeypatch, slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_EIGHT", slack_team_id="T1"),
    )
    client = _raw_client(engine, fake_embedder)

    authorize_resp = client.get("/slack/authorize-url", headers=_user_headers(8))
    state = dict(
        pair.split("=", 1) for pair in authorize_resp.json()["url"].split("?", 1)[1].split("&")
    )["state"]

    resp, done = _complete_link(client, state, 8)
    assert resp.status_code in (302, 307)
    assert done.status_code == 200

    with get_sessionmaker(engine)() as session:
        link = get_slack_link(session, 8)
        assert link is not None
        assert link.slack_user_id == "U_EIGHT"


def _complete_link(client, state: str, employee_id: int):
    """Callback -> pending token -> redeem in the caller's session (#494).

    The callback no longer links on its own: it has no session, so it cannot know
    which employee. Tests must travel both halves.
    """

    resp = client.get(
        "/slack/oauth/callback",
        params={"code": "a-real-looking-code", "state": state},
        follow_redirects=False,
    )
    location = resp.headers["location"]
    if "slack_pending=" not in location:
        return resp, None
    pending = location.split("slack_pending=", 1)[1]
    return resp, client.post(
        "/slack/link/complete",
        json={"pending_token": pending},
        headers=_user_headers(employee_id),
    )


def _state_for(client, employee_id: int) -> str:
    resp = client.get("/slack/authorize-url", headers=_user_headers(employee_id))
    query = resp.json()["url"].split("?", 1)[1]
    return dict(pair.split("=", 1) for pair in query.split("&"))["state"]


def test_oauth_callback_rejects_a_foreign_workspace(
    monkeypatch, slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    """A Slack identity from a workspace we did not install into must not link.

    Slack's OAuth will happily authenticate ANY workspace's user against this
    client id, so the team the token came back with is the only thing separating
    "a colleague" from "a stranger who found the URL". This matters most once
    the same callback becomes a LOGIN route (#406): without it, membership of
    any Slack workspace would be enough to sign in.
    """

    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", "T_OURS")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_OUTSIDER", slack_team_id="T_THEIRS"),
    )
    client = _raw_client(engine, fake_embedder)

    resp = client.get(
        "/slack/oauth/callback",
        params={"code": "c", "state": _state_for(client, 12)},
        follow_redirects=False,
    )

    assert resp.status_code in (302, 307)
    assert "slack=error" in resp.headers["location"]
    # Employee 12 is used by this test ALONE — 8 is already linked by the success
    # case above, so asserting "no link" there would depend on suite order.
    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, 12) is None


def test_oauth_callback_accepts_the_configured_workspace(
    monkeypatch, slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", "T_OURS")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_INSIDER", slack_team_id="T_OURS"),
    )
    client = _raw_client(engine, fake_embedder)

    _, done = _complete_link(client, _state_for(client, 9), 9)

    assert done.status_code == 200
    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, 9).slack_user_id == "U_INSIDER"


def test_oauth_callback_links_any_workspace_when_team_is_unset(
    monkeypatch, slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    # Unset stays permissive ON PURPOSE: this is the pre-#406 behaviour and the
    # dev/demo default, where no real workspace exists to name. The fail-closed
    # requirement belongs to the LOGIN route, which refuses to enable without it
    # — not here, where it would break every existing local setup.
    monkeypatch.delenv("TEKIJIN_SLACK_TEAM_ID", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_ANY", slack_team_id="T_WHATEVER"),
    )
    client = _raw_client(engine, fake_embedder)

    _, done = _complete_link(client, _state_for(client, 11), 11)

    assert done.status_code == 200


def test_a_link_stored_before_the_team_was_configured_is_ignored(
    monkeypatch, slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    """Turning the setting on must also neutralise rows already in the table.

    The callback only guards FUTURE links. On an existing install the table
    already holds rows from whatever workspace linked earlier, and every reader
    (/slack/status, ensure_pair_channel, the events + interactivity lookups)
    would keep treating them as valid. Filtering on read fixes those without a
    migration.
    """

    with get_sessionmaker(engine)() as session:
        upsert_slack_link(session, 13, slack_user_id="U_STALE", slack_team_id="T_OLD", now=NOW)
        session.commit()

    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", "T_OURS")
    get_settings.cache_clear()
    client = _raw_client(engine, fake_embedder)

    resp = client.get("/slack/status", headers=_user_headers(13))
    assert resp.json()["linked"] is False, "別ワークスペースの既存行が連携済みとして扱われている"

    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, 13, expected_team_id="T_OURS") is None
        # Still physically present — this is a read-time filter, not a delete.
        assert get_slack_link(session, 13) is not None


def test_oauth_callback_redirects_to_error_when_slack_account_already_linked_elsewhere(
    monkeypatch, slack_app_configured, seed_counts, engine, fake_embedder
) -> None:
    """slack_user_id is unique — completing OAuth for a Slack account already
    linked to a DIFFERENT employee must still redirect (never a bare 500)."""

    # 30/31: distinct from every employee id another test in this module links,
    # so this test's "never linked" assertion below can't be polluted by
    # another test's row (`engine` is a session-scoped fixture — the DB
    # persists across every test in the run, not just this file).
    with get_sessionmaker(engine)() as session:
        upsert_slack_link(session, 31, slack_user_id="U_SHARED", slack_team_id="T1", now=NOW)
        session.commit()

    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_SHARED", slack_team_id="T1"),
    )
    client = _raw_client(engine, fake_embedder)

    authorize_resp = client.get("/slack/authorize-url", headers=_user_headers(30))
    state = dict(
        pair.split("=", 1) for pair in authorize_resp.json()["url"].split("?", 1)[1].split("&")
    )["state"]

    resp, done = _complete_link(client, state, 30)
    assert resp.status_code in (302, 307)
    # 409 with a message, not a bare error: the collision is now surfaced at the
    # authenticated redeem step, where there IS a caller to explain it to.
    assert done.status_code == 409
    assert "他の社員" in done.json()["detail"]

    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, 31).slack_user_id == "U_SHARED"  # unchanged
        assert get_slack_link(session, 30) is None  # never linked
