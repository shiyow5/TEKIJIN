"""The OAuth `state` must be bound to the browser that started the flow (#494).

A signed, unexpired `state` proves only that WE minted it — not that the browser
completing the callback is the one that asked for it. Without that binding an
attacker mints a state carrying their OWN employee id, gets a victim to approve
Slack consent against it, and the callback attaches the VICTIM's Slack identity
to the ATTACKER's employee row. With Slack login on (#482) the victim then signs
in as the attacker on every subsequent "Sign in with Slack".

Proven executable before the fix; these hold it shut.
"""

from __future__ import annotations

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
from tekijin.data.slack_links import get_slack_link, get_slack_link_by_slack_user_id
from tekijin.slack.client import SlackIdentity

ATTACKER, VICTIM = 20, 21
TEAM = "T_OURS"


def _client(engine, embedder) -> TestClient:
    return TestClient(
        create_app(
            agent_service=AgentService(
                session_factory=get_sessionmaker(engine),
                checkpointer=MemorySaver(),
                embedder=embedder,
                intent_model=KeywordIntentModel(),
                sufficiency_model=RuleSufficiencyModel(),
                draft_model=TemplateDraftModel(),
            )
        )
    )


def _headers(employee_id: int) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(
            Principal(employee_id=employee_id, name="社員", dept=None, is_admin=False),
            secret=get_settings().auth_secret,
            ttl_hours=1,
        )
    }


def _state_from(url: str) -> str:
    return dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))["state"]


@pytest.fixture
def slack_configured(monkeypatch):
    monkeypatch.setenv("TEKIJIN_SLACK_CLIENT_ID", "cid")
    monkeypatch.setenv("TEKIJIN_SLACK_CLIENT_SECRET", "cs")
    monkeypatch.setenv("TEKIJIN_SLACK_REDIRECT_URI", "http://localhost:8000/slack/oauth/callback")
    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", TEAM)
    monkeypatch.setenv("TEKIJIN_SLACK_LOGIN_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_a_state_minted_by_someone_else_cannot_capture_my_slack_account(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    """The headline attack: the victim's browser never held the attacker's nonce."""

    attacker = _client(engine, fake_embedder)
    state = _state_from(
        attacker.get("/slack/authorize-url", headers=_headers(ATTACKER)).json()["url"]
    )

    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_VICTIM", slack_team_id=TEAM),
    )
    # A DIFFERENT browser: no cookies carried over from the attacker's client.
    victim = _client(engine, fake_embedder)
    resp = victim.get(
        "/slack/oauth/callback",
        params={"code": "victims-code", "state": state},
        follow_redirects=False,
    )

    assert "slack=error" in resp.headers["location"]
    with get_sessionmaker(engine)() as session:
        assert get_slack_link_by_slack_user_id(session, "U_VICTIM") is None, (
            "被害者のSlack IDが誰かの社員レコードに紐づいた"
        )
        assert get_slack_link(session, ATTACKER) is None


def test_the_same_browser_still_links_normally(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    # The fix must not break the real flow: one client, cookie jar intact.
    client = _client(engine, fake_embedder)
    state = _state_from(
        client.get("/slack/authorize-url", headers=_headers(ATTACKER)).json()["url"]
    )
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_SELF", slack_team_id=TEAM),
    )

    resp = client.get(
        "/slack/oauth/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )

    assert "slack=linked" in resp.headers["location"]
    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, ATTACKER).slack_user_id == "U_SELF"


def test_login_from_a_browser_that_did_not_start_the_flow_gets_no_token(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    """Login CSRF: the attacker's code+state, replayed into the victim's browser."""

    with get_sessionmaker(engine)() as session:
        from tekijin.data.slack_links import upsert_slack_link

        import datetime as dt

        upsert_slack_link(
            session, ATTACKER, slack_user_id="U_ATTACKER", slack_team_id=TEAM, now=dt.datetime.now()
        )
        session.commit()

    attacker = _client(engine, fake_embedder)
    state = _state_from(attacker.get("/slack/login-url").json()["url"])
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_ATTACKER", slack_team_id=TEAM),
    )

    victim = _client(engine, fake_embedder)
    resp = victim.get(
        "/slack/oauth/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )

    assert "slack_token" not in resp.headers["location"], (
        "他人が開始したフローで、被害者のブラウザにトークンが渡った"
    )


def test_the_nonce_cookie_is_not_readable_by_scripts_and_is_short_lived(
    slack_configured, seed_counts, engine, fake_embedder
) -> None:
    client = _client(engine, fake_embedder)
    resp = client.get("/slack/authorize-url", headers=_headers(ATTACKER))

    # Cookie attributes are case-insensitive (RFC 6265), so compare lowercased —
    # asserting the exact casing tests Starlette's formatting, not our intent.
    header = resp.headers.get("set-cookie", "").lower()
    assert "httponly" in header, f"HttpOnly が無い: {header}"
    assert "samesite=lax" in header, (
        f"SameSite=Lax が無い（Strictだとコールバックで送られない）: {header}"
    )
    assert "max-age=" in header, f"寿命が無い: {header}"
    assert "path=/slack" in header, f"パスが絞られていない: {header}"


def test_the_state_does_not_carry_the_nonce_itself(
    slack_configured, seed_counts, engine, fake_embedder
) -> None:
    # The state travels through Slack and shows up in logs; if it carried the
    # nonce in the clear, anyone who saw it could forge the cookie.
    import jwt

    client = _client(engine, fake_embedder)
    resp = client.get("/slack/authorize-url", headers=_headers(ATTACKER))
    state = _state_from(resp.json()["url"])
    cookie_value = resp.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    payload = jwt.decode(state, get_settings().auth_secret, algorithms=["HS256"])
    assert cookie_value not in str(payload), "nonce が state に平文で載っている"
