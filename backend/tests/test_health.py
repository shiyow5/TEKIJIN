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
    get_settings.cache_clear()

    client = TestClient(create_app())
    body = client.get("/health").json()

    assert body["env"] == "staging"

    get_settings.cache_clear()
