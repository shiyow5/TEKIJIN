"""LangGraph checkpointer selection: PostgresSaver (production) or MemorySaver.

``memory`` (default) is safe everywhere and needs no DB. ``postgres`` persists
sessions across restarts via ``langgraph-checkpoint-postgres`` over a connection
pool, and its schema is created once with ``.setup()``. If the postgres
checkpointer cannot be set up (no DB, missing dep, connection error) the factory
logs and falls back to MemorySaver, so the API always starts.

Imports of the postgres/psycopg stack are function-local so the default memory
path pulls none of them.
"""

from __future__ import annotations

import logging
from typing import Any

from tekijin.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _postgres_conn_string(database_url: str) -> str:
    # The langgraph postgres saver wants a raw psycopg conn string (no SQLAlchemy
    # ``+psycopg`` driver suffix).
    return database_url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def make_postgres_checkpointer(database_url: str) -> Any:  # pragma: no cover - needs a live DB
    """Build a pool-backed PostgresSaver and run its one-time schema setup."""

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        _postgres_conn_string(database_url),
        min_size=1,
        max_size=4,
        timeout=5.0,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    # ``row_factory=dict_row`` (set via kwargs above) makes this a dict-row pool at
    # runtime, which PostgresSaver requires; the psycopg generic type doesn't track
    # that through ``kwargs``, so the assignment is safe but needs the ignore.
    saver = PostgresSaver(pool)  # type: ignore[arg-type]
    saver.setup()
    return saver


def make_checkpointer(settings: Settings | None = None) -> Any:
    """Return the configured checkpointer, falling back to MemorySaver on failure."""

    settings = settings or get_settings()
    if settings.checkpointer_backend == "postgres":
        try:
            return make_postgres_checkpointer(settings.database_url)
        except Exception as exc:  # broad: any setup failure -> safe fallback
            logger.warning("PostgresSaver unavailable (%s); using MemorySaver", exc)

    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()
