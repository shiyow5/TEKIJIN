"""CLI entry point: ``python -m tekijin.eval`` — run the offline evaluation.

Builds the real pipeline over the seeded database and prints the metric report.
Requires ``TEKIJIN_DATABASE_URL`` (a seeded PostgreSQL+pgvector) and downloads
the sentence-transformers model on first use. Kept out of coverage (like
``main.py``): it is a thin wiring shell around the tested ``run_eval`` core.
"""

from __future__ import annotations

import datetime as dt
import sys

from tekijin.data.db import get_engine, get_sessionmaker
from tekijin.eval.dataset import load_eval_queries
from tekijin.eval.pipeline import build_pipeline_ranker
from tekijin.eval.runner import format_report, run_eval
from tekijin.retrieval.embedding import SentenceTransformerEmbedder


def main() -> int:
    settings_env = "TEKIJIN_DATABASE_URL"
    import os

    url = os.environ.get(settings_env)
    if not url:
        print(f"{settings_env} is required (point it at a seeded database)", file=sys.stderr)
        return 2

    queries = load_eval_queries()
    engine = get_engine(url)
    session = get_sessionmaker(engine)()
    try:
        embedder = SentenceTransformerEmbedder()
        ranker = build_pipeline_ranker(session, embedder, now=dt.datetime.now())  # noqa: DTZ005
        report = run_eval(queries, ranker)
    finally:
        session.close()
        engine.dispose()

    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
