"""Seed the database from the synthetic JSON fixtures.

Idempotent: every table is truncated (``RESTART IDENTITY CASCADE``) before rows
are re-inserted, so running the seed repeatedly converges on the same state.
Embeddings are left ``NULL`` (a later ingestion component fills them in).

CLI::

    python -m tekijin.data.seed

Prints per-table insert counts to stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tekijin.config import get_settings
from tekijin.data.db import (
    Base,
    create_all,
    ensure_pgvector,
    get_engine,
    get_sessionmaker,
    session_scope,
)
from tekijin.data.mappers import build_all

# Order in which to TRUNCATE — children before parents. CASCADE makes the exact
# order forgiving, but listing children first keeps intent clear.
#
# SECURITY: this tuple is the ONLY source of table names interpolated into the
# TRUNCATE statement. It is a hard-coded allow-list — never build it from user
# input, request data, or any external source, since the names are spliced into
# raw SQL (identifiers cannot be passed as bind parameters).
_TRUNCATE_ORDER: tuple[str, ...] = (
    "evidence",
    "person_topic_edges",
    "events",
    "eval_runs",
    "recommendations",
    "answers",
    "questions",
    "documents",
    "daily_reports",
    "ai_chat_history",
    "employee_chat_history",
    "project_members",
    "skills",
    "certifications",
    "employee_profiles",
    "projects",
    "employees",
)


def truncate_all(session: Session) -> None:
    """Empty every table and reset identity sequences."""

    tables = ", ".join(_TRUNCATE_ORDER)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def seed_session(session: Session, fixtures_dir: Path) -> dict[str, int]:
    """Truncate and re-insert every fixture group. Returns per-table counts."""

    truncate_all(session)
    counts: dict[str, int] = {}
    for logical_name, rows in build_all(fixtures_dir):
        session.add_all(rows)
        # Flush per group so FK-dependent inserts see their parents.
        session.flush()
        counts[logical_name] = len(rows)
    return counts


def apply_migrations(
    database_url: str | None = None,
    *,
    engine: Engine | None = None,
) -> None:
    """Bring the schema up to date WITHOUT touching data (non-destructive).

    Ensures the ``vector`` extension, creates any missing tables (``create_all``
    never drops or truncates), and runs the idempotent additive DDL in
    :func:`_apply_schema_upgrades`. Existing rows are retained. NOTE: an embedding
    column widened by a dimension change is reset to ``NULL`` (a model change
    invalidates old vectors); recompute with ``make embed``.

    This is the safe deploy-time path when a database already holds data — unlike
    :func:`run_seed`, which truncates and reloads fixtures. Exposed as a CLI via
    ``python -m tekijin.data.migrate``.
    """

    eng = engine if engine is not None else get_engine(database_url)
    ensure_pgvector(eng)
    create_all(eng)
    _apply_schema_upgrades(eng)


def run_seed(
    database_url: str | None = None,
    fixtures_dir: Path | None = None,
    *,
    engine: Engine | None = None,
) -> dict[str, int]:
    """Full seed pipeline: migrate schema, then TRUNCATE and load fixtures.

    DESTRUCTIVE: :func:`seed_session` truncates every table before re-inserting.
    For a data-preserving schema update use :func:`apply_migrations` instead.

    Pass ``engine`` to reuse an existing engine (preferred by callers that
    already hold one — a live ``Engine`` carries the real password, whereas
    ``str(engine.url)`` masks it and would produce a broken URL). Otherwise an
    engine is built from ``database_url`` (or the configured default).
    """

    settings = get_settings()
    eng = engine if engine is not None else get_engine(database_url)
    fixtures = fixtures_dir or settings.fixtures_dir

    apply_migrations(engine=eng)

    factory = get_sessionmaker(eng)
    with session_scope(factory) as session:
        counts = seed_session(session, fixtures)
    return counts


def _apply_schema_upgrades(engine: Engine) -> None:
    """Additive, idempotent DDL for columns ``create_all`` cannot add or change.

    The repo has no migration tool — ``create_all`` creates missing TABLES but
    never ALTERs an existing one. So a database seeded before a column was added
    (e.g. the Docker Compose persistent volume) would be missing it and break at
    runtime. ``ADD COLUMN IF NOT EXISTS`` makes re-seeding pick up new columns;
    new tables (e.g. ``eval_runs``) are handled by ``create_all`` itself.

    Column TYPE changes are likewise invisible to ``create_all``. When the
    embedding model changed (#63: e5-large 1024-d → Nemotron-3-Embed-1B 2048-d),
    an existing DB keeps its ``vector(1024)`` columns, which would reject the new
    2048-d vectors at ``make embed`` time. The guarded block below widens each
    embedding column to ``vector(2048)`` only when it is not already that width,
    dropping any stale (wrong-model) embeddings with ``USING NULL`` — they are
    recomputed by ``make embed``. Idempotent: a fresh ``create_all`` already
    builds ``vector(2048)`` so the block is a no-op there (no table rewrite).
    """

    # The target width is derived from the single source of truth
    # (``settings.embedding_dim``, which also drives ``Vector(EMBEDDING_DIM)`` in
    # models/tables.py) so a future dim change updates ``create_all`` and this
    # migration together. It is an internal ``int`` (never external input), so
    # interpolating it into the SQL is safe.
    dim = get_settings().embedding_dim
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE questions ADD COLUMN IF NOT EXISTS route VARCHAR(32)"))
        # The graph thread_id for an API-created question (responder inbox → handoff
        # deep link). Added for #123; older DBs get it here, fresh ones via create_all.
        conn.execute(text("ALTER TABLE questions ADD COLUMN IF NOT EXISTS session_id VARCHAR(64)"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_questions_session_id ON questions (session_id)")
        )
        # Runtime resolution timestamp (#97): so avg resolution time counts live
        # accepts / self-resolutions, not only seeded answers rows.
        conn.execute(text("ALTER TABLE questions ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP"))
        # Widen embedding columns to the current dim when an older DB is narrower.
        # Table/column names are a hard-coded allow-list spliced via format() —
        # never build them from external input (identifiers can't be bound).
        conn.execute(
            text(
                "DO $$\n"
                "DECLARE\n"
                "  tbl text;\n"
                "  cur text;\n"
                "BEGIN\n"
                "  FOREACH tbl IN ARRAY ARRAY['employee_profiles','questions',"
                "'answers','documents'] LOOP\n"
                "    SELECT format_type(atttypid, atttypmod) INTO cur\n"
                "      FROM pg_attribute\n"
                "      WHERE attrelid = tbl::regclass AND attname = 'embedding'\n"
                "        AND attnum > 0 AND NOT attisdropped;\n"
                f"    IF cur IS DISTINCT FROM 'vector({dim})' THEN\n"
                "      EXECUTE format(\n"
                f"        'ALTER TABLE %I ALTER COLUMN embedding TYPE vector({dim}) USING NULL',\n"
                "        tbl);\n"
                "    END IF;\n"
                "  END LOOP;\n"
                "END $$;"
            )
        )


def _format_counts(counts: dict[str, int]) -> str:
    lines = [f"  {name:<18} {count:>6}" for name, count in counts.items()]
    total = sum(counts.values())
    lines.append(f"  {'TOTAL':<18} {total:>6}")
    return "\n".join(lines)


def main() -> int:
    """CLI entry point. Seeds the configured database and prints counts."""

    # ``Base`` is referenced so the metadata is registered even if models were
    # not imported elsewhere in this process. Use an explicit raise (not
    # ``assert``) so the guard survives ``python -O``.
    if not Base.metadata.tables:
        raise RuntimeError("no tables registered on Base.metadata")
    counts = run_seed()
    print("Seeded TEKIJIN fixtures:")
    print(_format_counts(counts))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI
    sys.exit(main())
