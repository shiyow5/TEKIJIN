"""Tests for the /health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tekijin import __version__
from tekijin.app import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["env"] == "development"


def test_health_env_reflects_settings(monkeypatch) -> None:
    from tekijin.config import get_settings

    monkeypatch.setenv("TEKIJIN_APP_ENV", "staging")
    get_settings.cache_clear()

    client = TestClient(create_app())
    body = client.get("/health").json()

    assert body["env"] == "staging"

    get_settings.cache_clear()
