"""Tests for the /health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tekijin import __version__
from tekijin.app import create_app
from tekijin.config import get_settings


def test_health_returns_ok(monkeypatch) -> None:
    # Env vars take precedence over any .env file, so set the expected value
    # explicitly to make this deterministic regardless of a developer's .env.
    monkeypatch.setenv("TEKIJIN_APP_ENV", "development")
    get_settings.cache_clear()

    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["env"] == "development"

    get_settings.cache_clear()


def test_health_env_reflects_settings(monkeypatch) -> None:
    monkeypatch.setenv("TEKIJIN_APP_ENV", "staging")
    # Outside development the real service is fail-closed on three fronts, and this
    # test only cares about the env echo — so satisfy all guards:
    #   * embedder refuses unpinned remote code (#108) -> disable remote code;
    #   * checkpointer refuses a non-durable/failed backend in production (#180) ->
    #     inject a working checkpointer so create_app doesn't hard-fail.
    #   * auth refuses the default secret/admin password (#241) -> set real values.
    monkeypatch.setenv("TEKIJIN_EMBEDDING_TRUST_REMOTE_CODE", "false")
    monkeypatch.setenv("TEKIJIN_AUTH_SECRET", "test-secret-not-the-default")
    monkeypatch.setenv("TEKIJIN_ADMIN_PASSWORD", "test-admin-not-the-default")
    from langgraph.checkpoint.memory import MemorySaver

    import tekijin.api.factory as factory

    monkeypatch.setattr(factory, "make_checkpointer", lambda _s: MemorySaver())
    get_settings.cache_clear()

    client = TestClient(create_app())
    body = client.get("/health").json()

    assert body["env"] == "staging"

    get_settings.cache_clear()
