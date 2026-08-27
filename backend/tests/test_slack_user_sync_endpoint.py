"""``POST /slack/sync-users`` — the trigger for the directory sync (#406 step 3).

Deliberately an endpoint rather than a background thread. A daemon thread in
this codebase has already swallowed a crash once (`create_channel_link`'s
duplicate-key failure went unnoticed because nothing surfaced it), and a sync
that writes `slack_links` is the last place that should fail silently. An
endpoint gives the failure somewhere to go — a status code and a caller — and
`cron` supplies the "every hour" part without any new machinery.

The gate matters as much as the sync: #406 warns that registering everyone
before evidence extraction (#404) lands fills the roster with `topic_fit = 0`
colleagues, so this stays OFF until someone turns it on deliberately.
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
from tekijin.slack.client import SlackApiError

TEAM = "T_SYNC"


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


def _headers(*, is_admin: bool) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(
            Principal(
                employee_id=None if is_admin else 3,
                name="管理者" if is_admin else "社員",
                dept=None,
                is_admin=is_admin,
            ),
            secret=get_settings().auth_secret,
            ttl_hours=1,
        )
    }


@pytest.fixture
def sync_on(monkeypatch):
    monkeypatch.setenv("TEKIJIN_SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", TEAM)
    monkeypatch.setenv("TEKIJIN_SLACK_USER_SYNC_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def sync_off(monkeypatch):
    monkeypatch.setenv("TEKIJIN_SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", TEAM)
    monkeypatch.delenv("TEKIJIN_SLACK_USER_SYNC_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_the_sync_is_off_by_default(seed_counts, engine, fake_embedder, sync_off) -> None:
    """#406: registering everyone before #404 gives every new colleague
    `topic_fit = 0`, so the roster grows while the recommendation does not."""

    resp = _client(engine, fake_embedder).post("/slack/sync-users", headers=_headers(is_admin=True))

    assert resp.status_code == 503


def test_a_non_admin_cannot_trigger_the_sync(seed_counts, engine, fake_embedder, sync_on) -> None:
    resp = _client(engine, fake_embedder).post(
        "/slack/sync-users", headers=_headers(is_admin=False)
    )

    assert resp.status_code == 403


def test_an_anonymous_caller_cannot_trigger_the_sync(
    seed_counts, engine, fake_embedder, sync_on
) -> None:
    assert _client(engine, fake_embedder).post("/slack/sync-users").status_code == 401


def test_an_admin_runs_the_sync_and_gets_what_it_did(
    seed_counts, engine, fake_embedder, sync_on, monkeypatch
) -> None:
    """The one employee whose Slack profile matches gets linked; the bot, the
    guest and the stranger are reported rather than acted on."""

    monkeypatch.setattr(
        "tekijin.api.slack_routes.list_users",
        lambda **kw: [
            {
                "id": "U_REAL",
                "team_id": TEAM,
                "profile": {"email": "tanaka.taro@sample-tekijin.co.jp"},
            },
            {"id": "U_BOT", "team_id": TEAM, "is_bot": True, "profile": {"email": "b@x.jp"}},
            {"id": "U_OUT", "team_id": TEAM, "profile": {"email": "stranger@x.jp"}},
        ],
    )

    resp = _client(engine, fake_embedder).post("/slack/sync-users", headers=_headers(is_admin=True))

    assert resp.status_code == 200
    body = resp.json()
    assert body["linked"] == 1
    assert body["unlinked"] == 0
    assert body["skipped"]["not_a_member"] == 1
    assert body["skipped"]["no_matching_employee"] == 1

    # And it actually landed in the table, not just in the response.
    from tekijin.data.slack_links import get_slack_link_by_slack_user_id

    with get_sessionmaker(engine)() as session:
        link = get_slack_link_by_slack_user_id(session, "U_REAL", expected_team_id=TEAM)
        assert link is not None
        assert link.slack_team_id == TEAM


def test_a_slack_failure_becomes_a_502_not_a_silent_success(
    seed_counts, engine, fake_embedder, sync_on, monkeypatch
) -> None:
    """A missing scope is the likely first failure (`users:read.email` is easy to
    forget). Reporting 200 with zero links would look identical to a workspace
    where nobody matched."""

    def boom(**kw):
        raise SlackApiError("missing_scope")

    monkeypatch.setattr("tekijin.api.slack_routes.list_users", boom)

    resp = _client(engine, fake_embedder).post("/slack/sync-users", headers=_headers(is_admin=True))

    assert resp.status_code == 502


def test_the_sync_needs_a_bot_token(seed_counts, engine, fake_embedder, monkeypatch) -> None:
    monkeypatch.setenv("TEKIJIN_SLACK_USER_SYNC_ENABLED", "true")
    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", TEAM)
    monkeypatch.setenv("TEKIJIN_SLACK_BOT_TOKEN", "")
    get_settings.cache_clear()
    try:
        resp = _client(engine, fake_embedder).post(
            "/slack/sync-users", headers=_headers(is_admin=True)
        )
        assert resp.status_code == 503
    finally:
        get_settings.cache_clear()


def test_the_sync_refuses_to_run_without_a_configured_workspace(
    seed_counts, engine, fake_embedder, monkeypatch
) -> None:
    """Blank `slack_team_id` means "match nobody" in the planner. Running anyway
    would report a clean zero-link sync and hide the misconfiguration."""

    monkeypatch.setenv("TEKIJIN_SLACK_USER_SYNC_ENABLED", "true")
    monkeypatch.setenv("TEKIJIN_SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("TEKIJIN_SLACK_TEAM_ID", "")
    get_settings.cache_clear()
    try:
        resp = _client(engine, fake_embedder).post(
            "/slack/sync-users", headers=_headers(is_admin=True)
        )
        assert resp.status_code == 503
    finally:
        get_settings.cache_clear()
