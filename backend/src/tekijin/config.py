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
        # Absolute path to the repository-root .env (the one .env.example targets),
        # so it is read consistently regardless of the process working directory.
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,  # immutable singleton: mutation must not leak across requests
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

    # Whether to prepend e5-style ``query:`` / ``passage:`` prefixes when
    # embedding. Correct for the e5 family (the default model); other models
    # (e.g. ruri, BGE variants) must NOT receive these prefixes or retrieval
    # quality degrades. Kept configurable because the embedding model is still
    # being benchmarked — flip this off when switching to a non-e5 model.
    embedding_use_e5_prefix: bool = True

    # Dimensionality of the embedding vectors produced by ``embedding_model``.
    # ``intfloat/multilingual-e5-large`` emits 1024-d vectors; this drives the
    # width of every ``pgvector`` column so the schema and the model agree.
    embedding_dim: int = 1024

    # Directory holding synthetic fixtures used for development/testing.
    fixtures_dir: Path = _DEFAULT_FIXTURES_DIR

    # CORS allowed origins. Explicit (wildcard "*" + credentials is rejected by
    # browsers) and an immutable tuple so the cached singleton cannot be mutated.
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)


@lru_cache
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton."""
    return Settings()
