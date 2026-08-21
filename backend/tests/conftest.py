"""Shared pytest fixtures for the data-layer tests.

Database strategy:

* If ``TEKIJIN_DATABASE_URL`` is set (CI provides a pgvector service), use it.
* Otherwise fall back to an ephemeral, root-less PostgreSQL started by
  ``pgserver`` (bundles pgvector). If ``pgserver`` is unavailable, the
  DB-backed tests are skipped rather than failing.

Isolation / safety: every destructive operation (``CREATE``/``DROP`` tables,
``TRUNCATE``) is confined to a dedicated, throwaway schema (``tekijin_test``).
Even when ``TEKIJIN_DATABASE_URL`` points at a shared or persistent database,
the suite only ever creates and drops that one schema — it never touches the
developer's real tables, which may share our table names (``employees`` …). The
schema is created fresh at session start and dropped (``CASCADE``) at teardown.
CI's disposable service and the local ``pgserver`` instance both run the full
suite unchanged.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator

import pytest
from sqlalchemy import event, text

# Import models so their tables are registered on Base.metadata before any
# create_all/seed runs.
import tekijin.models.tables  # noqa: F401
from tekijin.config import get_settings
from tekijin.data.db import (
    Base,
    ensure_pgvector,
    get_engine,
    get_sessionmaker,
)
from tekijin.data.seed import run_seed

# Hard-coded, test-only schema name. It is spliced into DDL as an identifier
# (schema names cannot be bind parameters), so it must remain a literal constant
# and never derive from external input.
TEST_SCHEMA = "tekijin_test"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Yield a SQLAlchemy URL for a live PostgreSQL+pgvector database."""

    env_url = os.environ.get("TEKIJIN_DATABASE_URL")
    if env_url:
        yield env_url
        return

    pgserver = pytest.importorskip(
        "pgserver", reason="no TEKIJIN_DATABASE_URL and pgserver not installed"
    )
    tmp_dir = tempfile.mkdtemp(prefix="tekijin_pg_")
    server = pgserver.get_server(tmp_dir)
    # pgserver hands back a plain psycopg2-style URL; point SQLAlchemy at psycopg3.
    url = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    try:
        yield url
    finally:
        server.cleanup()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _drop_test_schema(database_url: str) -> None:
    admin = get_engine(database_url)
    try:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def engine(database_url: str):
    """Session-scoped engine whose work is confined to ``tekijin_test``.

    The extension lives at the database level (``public``); only the schema and
    its tables are created/dropped here, so the target database's own tables are
    never affected.
    """

    admin = get_engine(database_url)
    ensure_pgvector(admin)  # DB-level, idempotent; installs into public
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
    admin.dispose()

    eng = get_engine(database_url)

    # Every connection created by this engine works inside the test schema, with
    # public on the path so the `vector` type resolves.
    @event.listens_for(eng, "connect")
    def _set_search_path(dbapi_conn, _record):  # pragma: no cover - driver callback
        with dbapi_conn.cursor() as cur:
            cur.execute(f"SET search_path TO {TEST_SCHEMA}, public")

    # checkfirst=False so tables are always built in TEST_SCHEMA (the first entry
    # in search_path). With checkfirst=True, a same-named table in `public` on a
    # shared database would be seen via the search_path and creation skipped,
    # leaving our schema empty. The schema was just created fresh, so there is
    # nothing to collide with here.
    Base.metadata.create_all(eng, checkfirst=False)
    try:
        yield eng
    finally:
        eng.dispose()
        _drop_test_schema(database_url)


@pytest.fixture(scope="session")
def seed_counts(engine) -> dict[str, int]:
    """Seed the database once and expose the per-table insert counts.

    The live ``engine`` is passed directly (never ``str(engine.url)``, which
    masks the password and would yield a broken URL).
    """

    return run_seed(engine=engine, fixtures_dir=get_settings().fixtures_dir)


@pytest.fixture
def session(engine) -> Iterator:
    """A short-lived session bound to the shared engine (for read tests)."""

    factory = get_sessionmaker(engine)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
