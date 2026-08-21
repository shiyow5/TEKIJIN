"""Shared pytest fixtures for the data-layer tests.

Database strategy:

* If ``TEKIJIN_DATABASE_URL`` is set (CI provides a pgvector service), use it.
* Otherwise fall back to an ephemeral, root-less PostgreSQL started by
  ``pgserver`` (bundles pgvector). If ``pgserver`` is unavailable, the
  DB-backed tests are skipped rather than failing.

Isolation / safety: every destructive operation (``CREATE``/``DROP`` tables,
``TRUNCATE``) is confined to a dedicated, throwaway schema whose name is unique
per test run (``tekijin_test_<uuid4 hex>``). Even when ``TEKIJIN_DATABASE_URL``
points at a shared or persistent database, the suite only ever creates and drops
that one schema — it never touches the developer's real tables, which may share
our table names (``employees`` …), and two runs against the same database cannot
collide. The schema is created fresh at session start and dropped (``CASCADE``)
at teardown. CI's disposable service and the local ``pgserver`` instance both
run the full suite unchanged.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterator, Sequence

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

# Test-only schema name, unique per run so concurrent runs against the same
# database never collide. It is spliced into DDL as an identifier (schema names
# cannot be bind parameters), so it is built ONLY from a fixed prefix plus a
# uuid4 hex string — never from external input — and validated below.
TEST_SCHEMA = f"tekijin_test_{uuid.uuid4().hex}"
assert re.fullmatch(r"tekijin_test_[0-9a-f]{32}", TEST_SCHEMA), TEST_SCHEMA


def fake_vector(text: str, dim: int) -> list[float]:
    """Deterministic, L2-normalised bag-of-tokens embedding for tests.

    Each whitespace token lights up one dimension (chosen by hashing the token),
    so identical text yields an identical vector — a query embeds to exactly the
    same vector as a passage with the same words, giving cosine similarity 1.0 —
    and texts that share tokens are closer than texts that do not. No model or
    download required. This mirrors the contract real embedders satisfy well
    enough to exercise dense search and RRF fusion deterministically.
    """

    vec = [0.0] * dim
    for token in (text or "").split():
        digest = hashlib.md5(token.encode("utf-8")).hexdigest()  # noqa: S324 - not security
        vec[int(digest, 16) % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        # No tokens: return a fixed unit vector so pgvector cosine is defined.
        vec[0] = 1.0
        return vec
    return [x / norm for x in vec]


class FakeEmbedder:
    """In-memory :class:`~tekijin.retrieval.embedding.Embedder` for tests.

    Ignores ``kind`` on purpose: a query and a passage with the same text must
    map to the same vector so exact-match relevance is predictable.
    """

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def encode(self, texts: Sequence[str], *, kind: str = "passage") -> list[list[float]]:
        return [fake_vector(t, self.dim) for t in texts]


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    """A :class:`FakeEmbedder` sized to the configured embedding dimension."""

    return FakeEmbedder(get_settings().embedding_dim)


@pytest.fixture(scope="session")
def test_schema() -> str:
    """The per-run isolation schema name (for e.g. a PostgresSaver search_path)."""

    return TEST_SCHEMA


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Yield a SQLAlchemy URL for a live PostgreSQL+pgvector database.

    Order of preference:

    1. ``TEKIJIN_DATABASE_URL`` (CI provides a pgvector service).
    2. An ephemeral ``pgserver`` instance (local dev without Docker).

    If neither is available — e.g. Windows, where the ``pgserver`` wheel is not
    installed, and no database URL was provided — the DB-backed tests are
    skipped rather than failing.
    """

    env_url = os.environ.get("TEKIJIN_DATABASE_URL")
    if env_url:
        yield env_url
        return

    pgserver = pytest.importorskip(
        "pgserver",
        reason="no TEKIJIN_DATABASE_URL set and pgserver is unavailable "
        "(e.g. on Windows); set TEKIJIN_DATABASE_URL to run the DB tests",
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
    """Session-scoped engine whose work is confined to ``TEST_SCHEMA``.

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
