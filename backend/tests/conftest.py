"""Shared pytest fixtures for the data-layer tests.

Database strategy:

* If ``TEKIJIN_DATABASE_URL`` is set (CI provides a pgvector service), use it.
* Otherwise fall back to an ephemeral, root-less PostgreSQL started by
  ``pgserver`` (bundles pgvector). If ``pgserver`` is unavailable, the
  DB-backed tests are skipped rather than failing.

The schema is created once per session; the fixtures are seeded once and read
back by the repository tests (reads never mutate, so no per-test isolation is
needed).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator

import pytest

# Import models so their tables are registered on Base.metadata before any
# create_all/seed runs.
import tekijin.models.tables  # noqa: F401
from tekijin.config import get_settings
from tekijin.data.db import (
    create_all,
    drop_all,
    ensure_pgvector,
    get_engine,
    get_sessionmaker,
)
from tekijin.data.seed import run_seed


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


@pytest.fixture(scope="session")
def engine(database_url: str):
    """A session-scoped engine with the extension + schema created."""

    eng = get_engine(database_url)
    ensure_pgvector(eng)
    drop_all(eng)
    create_all(eng)
    try:
        yield eng
    finally:
        drop_all(eng)
        eng.dispose()


@pytest.fixture(scope="session")
def seed_counts(engine, database_url: str) -> dict[str, int]:
    """Seed the database once and expose the per-table insert counts."""

    return run_seed(database_url, get_settings().fixtures_dir)


@pytest.fixture
def session(engine) -> Iterator:
    """A short-lived session bound to the shared engine (for read tests)."""

    factory = get_sessionmaker(engine)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
