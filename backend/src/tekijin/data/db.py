"""Database engine, session factory, and schema helpers.

Thin SQLAlchemy 2.0 wiring. The engine is created lazily from
:func:`tekijin.config.get_settings` (or an explicit URL) so tests can point at a
disposable database. ``Base`` is the declarative base every ORM table extends.

The persistence target is PostgreSQL 16 + pgvector; :func:`ensure_pgvector`
creates the ``vector`` extension so the embedding columns can be created.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from tekijin.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def get_engine(database_url: str | None = None, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy :class:`Engine`.

    ``create_engine`` does not open a connection, so this is cheap and safe to
    call at import time. Pass ``database_url`` to override the configured URL
    (used by tests); otherwise ``settings.database_url`` is used.
    """

    url = database_url or get_settings().database_url
    return create_engine(url, echo=echo, future=True, pool_pre_ping=True)


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    """Return a :class:`sessionmaker` bound to ``engine`` (or a fresh engine)."""

    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


# Default application session factory, bound to the configured database.
SessionLocal: sessionmaker[Session] = get_sessionmaker()


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """Provide a transactional scope around a series of operations.

    Commits on success, rolls back on error, and always closes the session.
    """

    factory = session_factory or SessionLocal
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_pgvector(engine: Engine) -> None:
    """Create the ``vector`` extension if it does not already exist.

    Must run before :func:`create_all`, because the embedding columns reference
    the ``vector`` type provided by this extension.
    """

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def create_all(engine: Engine) -> None:
    """Create every table registered on :class:`Base`'s metadata."""

    Base.metadata.create_all(engine)
