#!/usr/bin/env python3
"""Compute and store dense embeddings for the seeded TEKIJIN corpus.

Runs the REAL local embedding model (``settings.embedding_model`` via
``sentence-transformers``), so it requires the heavy ML dependencies and is
meant to be run on a workstation / the DGX box — never in CI or the test suite.

Prerequisites::

    pip install -r backend/requirements.txt -r backend/requirements-ml.txt
    make seed          # populate rows (embeddings start NULL)

Then::

    make embed         # or: PYTHONPATH=backend/src python scripts/embed_fixtures.py

By default only rows whose embedding is still NULL are filled; pass
``--all`` to re-embed everything (e.g. after changing the model).
"""

from __future__ import annotations

import argparse
import sys

from tekijin.data.db import get_engine, get_sessionmaker, session_scope
from tekijin.retrieval.embedding import SentenceTransformerEmbedder
from tekijin.retrieval.indexing import embed_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        dest="only_missing",
        action="store_false",
        help="re-embed every row, not just those with a NULL embedding",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args(argv)

    embedder = SentenceTransformerEmbedder()
    factory = get_sessionmaker(get_engine())
    with session_scope(factory) as session:
        counts = embed_corpus(
            session,
            embedder,
            only_missing=args.only_missing,
            batch_size=args.batch_size,
        )

    print("Embedded rows:")
    for name, count in counts.items():
        print(f"  {name:<20} {count:>6}")
    print(f"  {'TOTAL':<20} {sum(counts.values()):>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
