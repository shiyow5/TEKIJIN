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

from sqlalchemy import text
from sqlalchemy.orm import Session

from tekijin.config import get_settings
from tekijin.data.db import (
    Base,
    create_all,
    ensure_pgvector,
    get_engine,
    session_scope,
)
from tekijin.data.mappers import build_all

# Order in which to TRUNCATE — children before parents. CASCADE makes the exact
# order forgiving, but listing children first keeps intent clear.
_TRUNCATE_ORDER: tuple[str, ...] = (
    "evidence",
    "person_topic_edges",
    "events",
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
) -> dict[str, int]:
    """Full seed pipeline: ensure extension, create schema, load fixtures."""

    settings = get_settings()
    engine = get_engine(database_url)
    fixtures = fixtures_dir or settings.fixtures_dir

    ensure_pgvector(engine)
    create_all(engine)

    from tekijin.data.db import get_sessionmaker

    factory = get_sessionmaker(engine)
    with session_scope(factory) as session:
        counts = seed_session(session, fixtures)
    return counts


def _format_counts(counts: dict[str, int]) -> str:
    lines = [f"  {name:<18} {count:>6}" for name, count in counts.items()]
    total = sum(counts.values())
    lines.append(f"  {'TOTAL':<18} {total:>6}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Seeds the configured database and prints counts."""

    # ``Base`` is referenced so the metadata is registered even if models were
    # not imported elsewhere in this process.
    assert Base.metadata.tables, "no tables registered"
    counts = run_seed()
    print("Seeded TEKIJIN fixtures:")
    print(_format_counts(counts))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI
    sys.exit(main())
