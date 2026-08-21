"""Tests for the /health endpoint."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from tekijin import __version__
from tekijin.app import create_app
from tekijin.config import get_settings


def test_health_returns_ok(monkeypatch, tmp_path) -> None:
    # Isolate from ambient TEKIJIN_* env and a developer's backend/.env so the
    # default-env assertion is deterministic (mirrors test_config isolation).
    for key in list(os.environ):
        if key.startswith("TEKIJIN_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
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
