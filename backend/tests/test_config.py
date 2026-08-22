"""Tests for tekijin.config."""

from __future__ import annotations

import os
from pathlib import Path

from tekijin.config import Settings, get_settings


def test_defaults(monkeypatch) -> None:
    # Isolate from ambient TEKIJIN_* env (the Makefile exports .env) and any
    # local .env file so this asserts the real code defaults, not the dev's env.
    for key in list(os.environ):
        if key.startswith("TEKIJIN_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.database_url == "postgresql+psycopg://tekijin:tekijin@localhost:5432/tekijin"
    assert settings.llm_base_url == "http://internship-dgx1:8080/v1"
    assert settings.llm_model == "Qwen3.6-35B-A3B-NVFP4"
    assert settings.llm_api_key == "dummy"
    assert settings.embedding_model == "nvidia/Nemotron-3-Embed-1B-BF16"
    assert settings.embedding_trust_remote_code is True
    assert settings.cors_origins == ("http://localhost:3000",)
    assert isinstance(settings.fixtures_dir, Path)
    assert settings.fixtures_dir.name == "synthetic"
    assert settings.fixtures_dir.parent.name == "fixtures"


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("TEKIJIN_LLM_MODEL", "custom-model")
    monkeypatch.setenv("TEKIJIN_APP_ENV", "production")

    settings = Settings()

    assert settings.llm_model == "custom-model"
    assert settings.app_env == "production"


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second

    get_settings.cache_clear()
