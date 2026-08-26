"""The FastAPI auto-docs must be OFF unless explicitly turned on (#457).

The backend is published to the internet for the Slack integration (#452), and
`/openapi.json` hands out every endpoint's path, parameters and types — enough to
plan against the routes that ARE protected. These assert the fail-closed default,
because the way this got shipped was nobody setting anything at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tekijin.app import create_app
from tekijin.config import get_settings

DOC_ROUTES = ("/docs", "/redoc", "/openapi.json")


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_doc_routes_are_closed_by_default(monkeypatch) -> None:
    # Nothing set: exactly the situation that exposed them on the DGX.
    monkeypatch.setenv("TEKIJIN_APP_ENV", "development")
    monkeypatch.delenv("TEKIJIN_EXPOSE_API_DOCS", raising=False)
    get_settings.cache_clear()

    client = TestClient(create_app())

    for route in DOC_ROUTES:
        assert client.get(route).status_code == 404, route


def test_doc_routes_open_when_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.setenv("TEKIJIN_APP_ENV", "development")
    monkeypatch.setenv("TEKIJIN_EXPOSE_API_DOCS", "true")
    get_settings.cache_clear()

    client = TestClient(create_app())

    for route in DOC_ROUTES:
        assert client.get(route).status_code == 200, route


def test_app_env_alone_does_not_open_the_docs(monkeypatch) -> None:
    # The DGX runs app_env=development for an unrelated reason (#108/#173), so
    # gating on app_env would have left production wide open. Guard against a
    # future refactor quietly reintroducing that coupling.
    monkeypatch.setenv("TEKIJIN_APP_ENV", "development")
    monkeypatch.setenv("TEKIJIN_EXPOSE_API_DOCS", "false")
    get_settings.cache_clear()

    client = TestClient(create_app())

    for route in DOC_ROUTES:
        assert client.get(route).status_code == 404, route


def test_health_still_reachable_when_docs_are_closed(monkeypatch) -> None:
    # Closing the docs must not take the liveness probe with it — the tunnel
    # script and deploy health check both poll /health.
    monkeypatch.setenv("TEKIJIN_APP_ENV", "development")
    monkeypatch.delenv("TEKIJIN_EXPOSE_API_DOCS", raising=False)
    get_settings.cache_clear()

    assert TestClient(create_app()).get("/health").status_code == 200
