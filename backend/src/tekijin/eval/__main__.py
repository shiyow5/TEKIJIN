"""CLI entry point: ``python -m tekijin.eval`` — run the offline evaluation.

Builds the real pipeline over the seeded database and prints the metric report.
Resolves the database URL and embedder settings through the SAME configuration
path as the rest of the backend (``get_settings()`` → ``.env`` via
pydantic-settings), so it works with the documented ``make seed`` workflow.
Requires the dense embeddings to be indexed (``make embed``) for the route /
dense metrics to be meaningful; warns loudly when they are absent. Kept out of
coverage (like ``main.py``): a thin wiring shell around the tested ``run_eval``.
"""

from __future__ import annotations

import datetime as dt
import sys

from sqlalchemy import func, select

from tekijin.config import get_settings
from tekijin.data.db import get_engine, get_sessionmaker
from tekijin.data.writes import insert_eval_run
from tekijin.eval.dataset import load_eval_queries
from tekijin.eval.pipeline import build_pipeline_ranker
from tekijin.eval.runner import format_report, run_eval
from tekijin.models.tables import EmployeeProfile
from tekijin.retrieval.embedding import SentenceTransformerEmbedder

# Fixed reference time so the scorer's recency / 7-day load window (and therefore
# the metrics) are reproducible run-to-run, independent of the wall clock. Anchored
# just after the fixtures' latest answer (2026-08-21) so the 7-day load window
# actually contains recent activity — otherwise the scorer's load term is never
# exercised (codex re-review #33).
EVAL_NOW = dt.datetime(2026, 8, 22, 0, 0, 0)


def _embeddings_indexed(session) -> bool:
    """True if any employee profile has a stored embedding vector."""

    count = session.scalar(
        select(func.count())
        .select_from(EmployeeProfile)
        .where(EmployeeProfile.embedding.isnot(None))
    )
    return bool(count)


def main() -> int:
    settings = get_settings()
    queries = load_eval_queries()
    engine = get_engine(settings.database_url)
    session = get_sessionmaker(engine)()
    try:
        indexed = _embeddings_indexed(session)
        if not indexed:
            print(
                "警告: 埋め込み索引が未生成です（`make embed`）。dense/route 指標は縮退します。",
                file=sys.stderr,
            )
        embedder = SentenceTransformerEmbedder(
            use_e5_prefix=settings.embedding_use_e5_prefix,
            query_prefix=settings.embedding_query_prefix,
            passage_prefix=settings.embedding_passage_prefix,
        )
        ranker = build_pipeline_ranker(session, embedder, now=EVAL_NOW)
        report = run_eval(queries, ranker)
        # Persist the snapshot so the dashboard (GET /dashboard) can surface the
        # latest 推薦精度 without re-running the evaluation on every request — but
        # NOT when embeddings are missing, so a degraded run can't silently
        # overwrite a valid prior snapshot on the dashboard.
        if indexed:
            insert_eval_run(session, report.metrics.as_dict())
            session.commit()
        else:
            print(
                "埋め込み未生成のため、この評価結果はダッシュボードに保存しません。",
                file=sys.stderr,
            )
    finally:
        session.close()
        engine.dispose()

    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
