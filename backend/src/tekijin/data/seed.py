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


def run_seed(
    database_url: str | None = None,
    fixtures_dir: Path | None = None,
    *,
    engine: Engine | None = None,
) -> dict[str, int]:
    """Full seed pipeline: ensure extension, create schema, load fixtures.

    Pass ``engine`` to reuse an existing engine (preferred by callers that
    already hold one — a live ``Engine`` carries the real password, whereas
    ``str(engine.url)`` masks it and would produce a broken URL). Otherwise an
    engine is built from ``database_url`` (or the configured default).
    """

    settings = get_settings()
    eng = engine if engine is not None else get_engine(database_url)
    fixtures = fixtures_dir or settings.fixtures_dir

    ensure_pgvector(eng)
    create_all(eng)
    _apply_schema_upgrades(eng)

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

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE questions ADD COLUMN IF NOT EXISTS route VARCHAR(32)"))
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
                "    IF cur IS DISTINCT FROM 'vector(2048)' THEN\n"
                "      EXECUTE format(\n"
                "        'ALTER TABLE %I ALTER COLUMN embedding TYPE vector(2048) USING NULL',\n"
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
