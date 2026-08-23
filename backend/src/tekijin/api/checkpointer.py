"""LangGraph checkpointer selection: PostgresSaver (production) or MemorySaver.

``memory`` (default) is safe for local dev and needs no DB. ``postgres`` persists
sessions across restarts via ``langgraph-checkpoint-postgres`` over a connection
pool, and its schema is created once with ``.setup()``. In development, if the
postgres checkpointer cannot be set up (no DB, missing dep, connection error) the
factory logs and falls back to MemorySaver so the API always starts. In production
(``app_env != development``) persistence is REQUIRED: a ``memory`` backend, or a
postgres setup that fails, is a hard error rather than a silent degrade (#180) —
in-memory sessions would be lost on the next restart.

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
        open=True,  # open the pool eagerly so setup() fails fast if the DB is down
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    # ``row_factory=dict_row`` (set via kwargs above) makes this a dict-row pool at
    # runtime, which PostgresSaver requires; the psycopg generic type doesn't track
    # that through ``kwargs``, so the assignment is safe but needs the ignore.
    saver = PostgresSaver(pool)  # type: ignore[arg-type]
    saver.setup()
    return saver


def make_checkpointer(settings: Settings | None = None) -> Any:
    """Return the configured checkpointer.

    Development is lenient: a Postgres setup failure (or ``memory`` backend)
    degrades to MemorySaver so the API always starts. **Production is fail-closed
    on durability** (#180): in-memory sessions would vanish on the next restart, so
    outside development a ``memory`` backend — or a Postgres setup that fails — is a
    hard error rather than a silent degrade. Set ``TEKIJIN_APP_ENV=development`` for
    the lenient local behavior.
    """

    settings = settings or get_settings()
    is_production = settings.app_env != "development"

    if settings.checkpointer_backend == "postgres":
        try:
            return make_postgres_checkpointer(settings.database_url)
        except Exception as exc:  # broad: any setup failure
            if is_production:
                raise RuntimeError(
                    f"PostgresSaver setup failed in production (app_env={settings.app_env!r}): "
                    f"{exc}. Sessions must persist — fix the database connection, or set "
                    "TEKIJIN_APP_ENV=development to allow the in-memory fallback."
                ) from exc
            logger.warning("PostgresSaver unavailable (%s); using MemorySaver", exc)
    elif is_production:
        # backend == "memory" outside development: a restart would drop every
        # in-flight session (durability regression for a multi-user deployment).
        raise RuntimeError(
            f"checkpointer_backend='memory' is not allowed in production "
            f"(app_env={settings.app_env!r}); sessions would be lost on restart. Set "
            "TEKIJIN_CHECKPOINTER_BACKEND=postgres, or TEKIJIN_APP_ENV=development for local use."
        )

    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()
