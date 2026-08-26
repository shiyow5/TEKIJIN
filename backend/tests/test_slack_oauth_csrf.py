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
from tekijin.data.slack_links import get_slack_link
from tekijin.slack.client import SlackIdentity

ATTACKER, VICTIM = 20, 21
# Dedicated to the initiator-binding tests: 20/21 are linked by other tests in
# this file, so reusing them would make those assertions order-dependent.
STARTER, OTHER = 22, 23
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


def test_a_link_url_forwarded_to_someone_else_links_nobody(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    """A forwarded link URL is inert: it names a starter who is not the finisher.

    Refusing is deliberate rather than "link the finisher to themselves" — a link
    the user did not initiate should not silently happen just because they opened
    a page.
    """

    attacker = _client(engine, fake_embedder)
    state = _state_from(
        attacker.get("/slack/authorize-url", headers=_headers(STARTER)).json()["url"]
    )
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_FORWARDED", slack_team_id=TEAM),
    )

    victim = _client(engine, fake_embedder)
    resp = victim.get(
        "/slack/oauth/callback",
        params={"code": "victims-code", "state": state},
        follow_redirects=False,
    )
    pending = resp.headers["location"].split("slack_pending=", 1)[1]

    assert (
        victim.post(
            "/slack/link/complete", json={"pending_token": pending}, headers=_headers(OTHER)
        ).status_code
        == 403
    )

    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, OTHER) is None
        assert get_slack_link(session, STARTER) is None


def test_the_pending_token_alone_does_not_link_anyone(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    # No bearer token -> no employee -> nothing to attach to.
    client = _client(engine, fake_embedder)
    state = _state_from(
        client.get("/slack/authorize-url", headers=_headers(ATTACKER)).json()["url"]
    )
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_X", slack_team_id=TEAM),
    )
    resp = client.get(
        "/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    pending = resp.headers["location"].split("slack_pending=", 1)[1]

    assert client.post("/slack/link/complete", json={"pending_token": pending}).status_code == 401


def test_the_normal_link_flow_still_works(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    client = _client(engine, fake_embedder)
    state = _state_from(
        client.get("/slack/authorize-url", headers=_headers(ATTACKER)).json()["url"]
    )
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_SELF", slack_team_id=TEAM),
    )
    resp = client.get(
        "/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    pending = resp.headers["location"].split("slack_pending=", 1)[1]

    assert (
        client.post(
            "/slack/link/complete", json={"pending_token": pending}, headers=_headers(ATTACKER)
        ).status_code
        == 200
    )
    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, ATTACKER).slack_user_id == "U_SELF"


def test_login_from_a_browser_that_did_not_start_the_flow_gets_no_token(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    """Login CSRF: the attacker's code+state, replayed into the victim's browser.

    Login is the one flow that mints a session out of nothing, so it is the one
    that needs the nonce cookie.
    """

    import datetime as dt

    from tekijin.data.slack_links import upsert_slack_link

    with get_sessionmaker(engine)() as session:
        upsert_slack_link(
            session, ATTACKER, slack_user_id="U_ATTACKER", slack_team_id=TEAM, now=dt.datetime.now()
        )
        session.commit()

    attacker = _client(engine, fake_embedder)
    hop = attacker.get("/slack/oauth/start", follow_redirects=False)
    state = _state_from(hop.headers["location"])
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_ATTACKER", slack_team_id=TEAM),
    )

    victim = _client(engine, fake_embedder)
    resp = victim.get(
        "/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )

    assert "slack_token" not in resp.headers["location"], (
        "他人が開始したフローで、被害者のブラウザにトークンが渡った"
    )


def test_a_present_but_wrong_nonce_is_rejected(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    """Not just "no cookie" — a cookie that exists but does not match must fail.

    Without this, an implementation that only checked for the cookie's PRESENCE
    would pass every other test here.
    """

    import datetime as dt

    from tekijin.data.slack_links import upsert_slack_link

    with get_sessionmaker(engine)() as session:
        upsert_slack_link(
            session, ATTACKER, slack_user_id="U_ATTACKER", slack_team_id=TEAM, now=dt.datetime.now()
        )
        session.commit()

    attacker = _client(engine, fake_embedder)
    state = _state_from(
        attacker.get("/slack/oauth/start", follow_redirects=False).headers["location"]
    )

    victim = _client(engine, fake_embedder)
    # A *different* live nonce, from the victim's own start — present, but wrong.
    victim.get("/slack/oauth/start", follow_redirects=False)
    assert victim.cookies.get("tekijin_oauth_state"), "前提: 被害者にもCookieがある"

    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_ATTACKER", slack_team_id=TEAM),
    )
    resp = victim.get(
        "/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )

    assert "slack_token" not in resp.headers["location"]


def test_the_nonce_cookie_is_issued_by_the_callback_origin(
    slack_configured, seed_counts, engine, fake_embedder
) -> None:
    """Issued at /slack/oauth/start, which lives on the SAME host as the callback.

    Issuing it from the API origin instead was the bug: the app calls the API on
    a different host from the tunnel Slack returns to, so the browser would never
    send it back and every login would fail the binding check (#494).
    """

    client = _client(engine, fake_embedder)
    resp = client.get("/slack/oauth/start", follow_redirects=False)

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
    resp = client.get("/slack/oauth/start", follow_redirects=False)
    state = _state_from(resp.headers["location"])
    cookie_value = resp.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    payload = jwt.decode(state, get_settings().auth_secret, algorithms=["HS256"])
    assert cookie_value not in str(payload), "nonce が state に平文で載っている"


# --- the flow must be finished by whoever started it ------------------------- #
#
# Both halves carry an identity: the state names the employee who STARTED the
# link, and the pending token names the Slack account that CONSENTED. Every
# attack found so far has been the same shape — take one half from the attacker
# and the other from the victim — so the check is that they match.


def test_a_pending_token_cannot_be_redeemed_by_a_different_employee(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    """Attacker consents as THEMSELVES, victim redeems: the victim's row would
    otherwise end up pointing at the attacker's Slack account, handing them the
    victim's DMs and the ability to post as them."""

    client = _client(engine, fake_embedder)
    state = _state_from(client.get("/slack/authorize-url", headers=_headers(STARTER)).json()["url"])
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_ATTACKER_REAL", slack_team_id=TEAM),
    )
    resp = client.get(
        "/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    pending = resp.headers["location"].split("slack_pending=", 1)[1]

    stolen = client.post(
        "/slack/link/complete", json={"pending_token": pending}, headers=_headers(OTHER)
    )

    assert stolen.status_code == 403, "他人が開始した連携を引き換えられた"
    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, OTHER) is None


def test_a_victims_consent_cannot_land_on_the_initiators_row(
    monkeypatch, slack_configured, seed_counts, engine, fake_embedder
) -> None:
    """The mirror image: attacker starts, victim consents. Neither half alone is
    enough, so this must fail too."""

    client = _client(engine, fake_embedder)
    state = _state_from(client.get("/slack/authorize-url", headers=_headers(STARTER)).json()["url"])
    monkeypatch.setattr(
        "tekijin.api.slack_routes.exchange_code",
        lambda **kw: SlackIdentity(slack_user_id="U_OTHERS_SLACK", slack_team_id=TEAM),
    )
    resp = client.get(
        "/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    pending = resp.headers["location"].split("slack_pending=", 1)[1]

    # The other person's browser auto-redeems on /chat, as their own session.
    assert (
        client.post(
            "/slack/link/complete", json={"pending_token": pending}, headers=_headers(OTHER)
        ).status_code
        == 403
    )
    with get_sessionmaker(engine)() as session:
        assert get_slack_link(session, STARTER) is None, "開始者の行に他人のSlackが載った"
        assert get_slack_link(session, OTHER) is None
