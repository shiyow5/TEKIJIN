"""Integration tests for auth against the live seeded DB (#241): login, /me,
logout, admin gating, non-admin identity binding, and SSE token transport."""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from tekijin.agent.stubs import KeywordIntentModel, RuleSufficiencyModel, TemplateDraftModel
from tekijin.api.service import AgentService
from tekijin.app import create_app
from tekijin.config import get_settings
from tekijin.data.db import get_sessionmaker
from tekijin.models.tables import Employee


def _raw_client(engine, embedder) -> TestClient:
    """A TestClient with NO default auth header (each test authenticates itself)."""

    service = AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=MemorySaver(),
        embedder=embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=TemplateDraftModel(),
    )
    return TestClient(create_app(agent_service=service))


def _an_employee(engine) -> Employee:
    with get_sessionmaker(engine)() as session:
        return session.scalars(select(Employee).order_by(Employee.id)).first()


def _login(client: TestClient, email: str, password: str):
    return client.post("/auth/login", json={"email": email, "password": password})


# --- login ------------------------------------------------------------------ #
def test_user_login_succeeds_with_demo_password(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    emp = _an_employee(engine)
    settings = get_settings()

    resp = _login(client, emp.email, settings.demo_user_password)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer" and body["access_token"]
    assert body["principal"]["is_admin"] is False
    assert body["principal"]["id"] == f"E{emp.id:03d}"


def test_admin_login_succeeds_and_is_not_an_employee(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    settings = get_settings()

    resp = _login(client, settings.admin_email, settings.admin_password)
    assert resp.status_code == 200
    principal = resp.json()["principal"]
    assert principal["is_admin"] is True
    assert principal["id"] is None  # admin is not a DB employee


def test_login_wrong_password_is_401(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    emp = _an_employee(engine)
    resp = _login(client, emp.email, "definitely-wrong")
    assert resp.status_code == 401


def test_login_unknown_email_is_401(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    resp = _login(client, "nobody@nowhere.example", "whatever")
    assert resp.status_code == 401


def test_login_rate_limited_after_repeated_failures(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    emp = _an_employee(engine)
    limit = get_settings().login_max_attempts
    for _ in range(limit):
        assert _login(client, emp.email, "wrong").status_code == 401
    # Next attempt is throttled BEFORE the password is checked — even a correct
    # password gets 429 while the window is hot.
    throttled = _login(client, emp.email, get_settings().demo_user_password)
    assert throttled.status_code == 429
    assert throttled.headers.get("Retry-After")


# --- /me and /logout -------------------------------------------------------- #
def test_me_echoes_principal_and_requires_auth(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    assert client.get("/auth/me").status_code == 401  # no token

    emp = _an_employee(engine)
    token = _login(client, emp.email, get_settings().demo_user_password).json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["id"] == f"E{emp.id:03d}"


def test_logout_is_ok(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    resp = client.post("/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["status"] == "logged_out"


# --- protected endpoints require a token ------------------------------------ #
def test_unauthenticated_requests_are_401(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    assert client.get("/dashboard").status_code == 401
    assert client.get("/employees").status_code == 401
    assert client.get("/questions?asker_id=1").status_code == 401
    assert client.get("/inbox?responder_id=1").status_code == 401
    assert client.get("/events/whatever").status_code == 401


def test_garbage_token_is_401(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


# --- admin gating ----------------------------------------------------------- #
def test_dashboard_and_employees_are_admin_only(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    settings = get_settings()
    emp = _an_employee(engine)

    user_token = _login(client, emp.email, settings.demo_user_password).json()["access_token"]
    user_h = {"Authorization": f"Bearer {user_token}"}
    assert client.get("/dashboard", headers=user_h).status_code == 403
    assert client.get("/employees", headers=user_h).status_code == 403

    admin_token = _login(client, settings.admin_email, settings.admin_password).json()[
        "access_token"
    ]
    admin_h = {"Authorization": f"Bearer {admin_token}"}
    assert client.get("/dashboard", headers=admin_h).status_code == 200
    assert client.get("/employees", headers=admin_h).status_code == 200


# --- non-admin identity binding --------------------------------------------- #
def test_user_cannot_act_as_another_employee(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    emp = _an_employee(engine)
    token = _login(client, emp.email, get_settings().demo_user_password).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    other = emp.id + 1
    # Own id: allowed (200); someone else's: 403.
    assert client.get(f"/questions?asker_id={emp.id}", headers=headers).status_code == 200
    assert client.get(f"/questions?asker_id={other}", headers=headers).status_code == 403
    assert client.get(f"/inbox?responder_id={other}", headers=headers).status_code == 403
    ask = client.post(
        "/ask",
        json={"asker_id": other, "question": "help me", "session_id": "sX"},
        headers=headers,
    )
    assert ask.status_code == 403


# --- SSE token-in-query transport ------------------------------------------- #
def test_events_accepts_token_as_query_param(seed_counts, engine, fake_embedder) -> None:
    client = _raw_client(engine, fake_embedder)
    emp = _an_employee(engine)
    token = _login(client, emp.email, get_settings().demo_user_password).json()["access_token"]

    # No token → 401. With ?token= (EventSource cannot set headers) auth passes,
    # so a missing session surfaces as 404 (not 401).
    assert client.get("/events/nope").status_code == 401
    assert client.get(f"/events/nope?token={token}").status_code == 404
