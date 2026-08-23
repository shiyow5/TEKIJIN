"""LangGraph checkpointer selection: PostgresSaver (production) or MemorySaver.

``memory`` (default) is safe for local dev and needs no DB. ``postgres`` persists
sessions across restarts via ``langgraph-checkpoint-postgres`` over a connection
pool, and its schema is created once with ``.setup()``.

When durability is NOT enforced (local dev), a ``memory`` backend — or a postgres
setup that fails — degrades to MemorySaver so the API always starts.
When it IS enforced (``settings.durability_enforced()``; see ``strict_durability``)
both are a hard error instead of a silent degrade (#180): in-memory sessions would
be lost on the next restart. Enforcement derives from ``app_env`` by default but is
a separate ``TEKIJIN_STRICT_DURABILITY`` knob, so it can be turned on where
``app_env`` must stay ``development`` for other reasons (#108/#173).

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
    # Decoupled from app_env on purpose (#180 review): the DGX host runs
    # app_env=development for an unrelated embedding reason (#108/#173), so tie
    # durability enforcement to its own knob instead.
    enforced = settings.durability_enforced()

    if settings.checkpointer_backend == "postgres":
        try:
            return make_postgres_checkpointer(settings.database_url)
        except Exception as exc:  # broad: any setup failure
            if enforced:
                raise RuntimeError(
                    f"PostgresSaver setup failed with durability enforced: {exc}. Sessions must "
                    "persist — fix the database connection, or set TEKIJIN_STRICT_DURABILITY=false "
                    "to allow the in-memory fallback."
                ) from exc
            logger.warning("PostgresSaver unavailable (%s); using MemorySaver", exc)
    elif enforced:
        # backend == "memory" with durability enforced: a restart would drop every
        # in-flight session (durability regression for a multi-user deployment).
        raise RuntimeError(
            "checkpointer_backend='memory' is not allowed when durability is enforced; "
            "sessions would be lost on restart. Set TEKIJIN_CHECKPOINTER_BACKEND=postgres, or "
            "TEKIJIN_STRICT_DURABILITY=false to allow the in-memory fallback."
        )

    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()
