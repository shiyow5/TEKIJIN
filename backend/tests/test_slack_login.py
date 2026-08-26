"""Sign in with Slack as a LOGIN route (#406 案A).

Distinct from account LINKING (test_slack_integration.py): linking starts from an
already-authenticated session and attaches a Slack identity to it, whereas this
starts from no session at all and hands back a bearer token. That inversion is
what makes the workspace check load-bearing — without it, membership of ANY
Slack workspace would be enough to sign in.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from tekijin.agent.stubs import KeywordIntentModel, RuleSufficiencyModel, TemplateDraftModel
from tekijin.api.service import AgentService
from tekijin.app import create_app
from tekijin.config import get_settings
from tekijin.data.db import get_sessionmaker
from tekijin.data.slack_links import upsert_slack_link
from tekijin.slack.client import SlackIdentity

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)
TEAM = "T_OURS"


def _client(engine, fake_embedder) -> TestClient:
    service = AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=MemorySaver(),
        embedder=fake_embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=TemplateDraftModel(),
    )
    return TestClient(create_app(agent_service=service))


@pytest.fixture
def slack_login_on(monkeypatch):
    monkeypatch.setenv("TEKIJIN_SLACK_CLIENT_ID", "cid")
    monkeypatch.setenv("TEKIJIN_SLACK_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("TEKIJIN_SLACK_REDIRECT_URI", "http://localhost:8000/slack/oauth/callback")
    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", TEAM)
    monkeypatch.setenv("TEKIJIN_SLACK_LOGIN_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _login_state(client) -> str:
    query = client.get("/slack/login-url").json()["url"].split("?", 1)[1]
    return dict(pair.split("=", 1) for pair in query.split("&"))["state"]


def _link(engine, employee_id: int, slack_user_id: str, team: str = TEAM) -> None:
    with get_sessionmaker(engine)() as session:
        upsert_slack_link(
            session, employee_id, slack_user_id=slack_user_id, slack_team_id=team, now=NOW
        )
        session.commit()


# --- the fail-closed startup guard ------------------------------------------ #


def test_startup_refuses_slack_login_without_a_workspace(monkeypatch, engine, fake_embedder):
    """Enabling Slack login while `slack_team_id` is blank must not boot.

    Blank means "accept any workspace" for LINKING (#473, deliberately permissive
    for local demos). Inheriting that here would make any Slack user anywhere a
    valid login, so the combination is refused rather than silently allowed.
    """

    monkeypatch.setenv("TEKIJIN_SLACK_LOGIN_ENABLED", "true")
    monkeypatch.delenv("TEKIJIN_SLACK_TEAM_ID", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="TEKIJIN_SLACK_TEAM_ID"):
            _client(engine, fake_embedder)
    finally:
        get_settings.cache_clear()


# --- GET /slack/login-url ---------------------------------------------------- #


def test_login_url_is_unavailable_while_disabled(monkeypatch, seed_counts, engine, fake_embedder):
    monkeypatch.delenv("TEKIJIN_SLACK_LOGIN_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        assert _client(engine, fake_embedder).get("/slack/login-url").status_code == 503
    finally:
        get_settings.cache_clear()


def test_login_url_needs_no_authentication(slack_login_on, seed_counts, engine, fake_embedder):
    # The whole point: the caller has no token yet.
    resp = _client(engine, fake_embedder).get("/slack/login-url")

    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://slack.com/oauth/v2/authorize")


# --- GET /slack/oauth/callback (login) --------------------------------------- #


def test_login_hands_back_a_working_token_in_the_url_FRAGMENT(
    monkeypatch, slack_login_on, seed_counts, engine, fake_embedder
):
    """The token must ride in the fragment, never the query string.

    A query parameter is written to the server access log verbatim — the OAuth
    `code` already shows up there. A fragment is never sent to a server at all,
    so it cannot be logged by us or by anything between.
    """

    _link(engine, 5, "U_FIVE")
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_FIVE", slack_team_id=TEAM),
    )
    client = _client(engine, fake_embedder)

    resp = client.get(
        "/slack/oauth/callback",
        params={"code": "c", "state": _login_state(client)},
        follow_redirects=False,
    )

    location = resp.headers["location"]
    assert "#" in location, "トークンがフラグメントに載っていない"
    query, fragment = location.split("#", 1)
    assert "token" not in query, f"トークンがクエリに漏れている: {query}"
    token = dict(pair.split("=", 1) for pair in fragment.split("&"))["slack_token"]

    # Round-trip: the token must actually authenticate as employee 5.
    me = client.get("/slack/status", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["linked"] is True


def test_login_rejects_an_unlinked_slack_user(
    monkeypatch, slack_login_on, seed_counts, engine, fake_embedder
):
    # Nobody has claimed this Slack id, so there is no employee to become.
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_NOBODY", slack_team_id=TEAM),
    )
    client = _client(engine, fake_embedder)

    resp = client.get(
        "/slack/oauth/callback",
        params={"code": "c", "state": _login_state(client)},
        follow_redirects=False,
    )

    assert "slack_token" not in resp.headers["location"]
    # Distinguishable from a generic failure on purpose: "your Slack account is
    # not attached to an employee yet" is actionable, "error" is not.
    assert "slack=unlinked" in resp.headers["location"]


def test_login_rejects_a_foreign_workspace_even_when_that_slack_id_is_linked(
    monkeypatch, slack_login_on, seed_counts, engine, fake_embedder
):
    """The dangerous case: a stranger whose Slack id collides is still refused.

    Guards the property that makes案A safe — workspace membership is the
    authorisation boundary, so it is checked before the identity is trusted.
    """

    _link(engine, 6, "U_SIX")
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_SIX", slack_team_id="T_THEIRS"),
    )
    client = _client(engine, fake_embedder)

    resp = client.get(
        "/slack/oauth/callback",
        params={"code": "c", "state": _login_state(client)},
        follow_redirects=False,
    )

    assert "slack_token" not in resp.headers["location"]


def test_a_link_state_cannot_be_replayed_as_a_login_state(
    monkeypatch, slack_login_on, seed_counts, engine, fake_embedder
):
    """`purpose` must separate the two flows.

    Both are signed with the same secret, so without the claim a LINK state —
    obtainable by any logged-in user — would be usable to mint a token.
    """

    from tekijin.api import slack_routes

    link_state = slack_routes._encode_state(
        purpose="slack_link", employee_id=7, secret=get_settings().auth_secret
    )
    _link(engine, 7, "U_SEVEN")
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kwargs: SlackIdentity(slack_user_id="U_SEVEN", slack_team_id=TEAM),
    )
    client = _client(engine, fake_embedder)

    resp = client.get(
        "/slack/oauth/callback",
        params={"code": "c", "state": link_state},
        follow_redirects=False,
    )

    # It is a valid LINK, so it links — but it must not hand back a token.
    assert "slack_token" not in resp.headers["location"]


def test_login_is_refused_while_disabled_even_with_a_valid_state(
    monkeypatch, seed_counts, engine, fake_embedder
):
    monkeypatch.setenv("TEKIJIN_SLACK_CLIENT_ID", "cid")
    monkeypatch.setenv("TEKIJIN_SLACK_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("TEKIJIN_SLACK_REDIRECT_URI", "http://localhost:8000/slack/oauth/callback")
    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", TEAM)
    monkeypatch.setenv("TEKIJIN_SLACK_LOGIN_ENABLED", "true")
    get_settings.cache_clear()
    from tekijin.api import slack_routes

    state = slack_routes._encode_state(purpose="slack_login", secret=get_settings().auth_secret)

    monkeypatch.setenv("TEKIJIN_SLACK_LOGIN_ENABLED", "false")
    get_settings.cache_clear()
    try:
        _link(engine, 8, "U_EIGHT_L")
        monkeypatch.setattr(
            "tekijin.api.slack_routes.exchange_code",
            lambda **kwargs: SlackIdentity(slack_user_id="U_EIGHT_L", slack_team_id=TEAM),
        )
        client = _client(engine, fake_embedder)
        resp = client.get(
            "/slack/oauth/callback",
            params={"code": "c", "state": state},
            follow_redirects=False,
        )
        assert "slack_token" not in resp.headers["location"]
    finally:
        get_settings.cache_clear()
