"""Application configuration.

Settings are loaded from environment variables (prefix ``TEKIJIN_``) with
sensible defaults for local development. Use :func:`get_settings` to obtain a
cached singleton.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root, resolved relative to this file:
#   <repo>/backend/src/tekijin/config.py -> parents[3] == <repo>
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FIXTURES_DIR = _REPO_ROOT / "fixtures" / "synthetic"


class Settings(BaseSettings):
    """Runtime settings for the TEKIJIN backend.

    Field values are read from ``TEKIJIN_*`` environment variables when present,
    otherwise the defaults below are used.
    """

    model_config = SettingsConfigDict(
        env_prefix="TEKIJIN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"

    # Database (connection code lands in a later component).
    database_url: str = "postgresql+psycopg://tekijin:tekijin@localhost:5432/tekijin"

    # LLM serving via vLLM (OpenAI-compatible /v1). Real client wiring: #31.
    llm_base_url: str = "http://internship-dgx1:8080/v1"
    llm_model: str = "Qwen3.6-35B-A3B-NVFP4"
    llm_api_key: str = "dummy"

    # Embedding model used by the retrieval component (later).
    embedding_model: str = "intfloat/multilingual-e5-large"

    # Directory holding synthetic fixtures used for development/testing.
    fixtures_dir: Path = _DEFAULT_FIXTURES_DIR

    # CORS allowed origins. Explicit list because wildcard "*" combined with
    # credentialed requests is rejected by browsers (see app.create_app).
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton."""
    return Settings()
