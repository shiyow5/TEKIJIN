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

from tekijin.config import get_settings
from tekijin.data.db import get_engine, get_sessionmaker, session_scope
from tekijin.retrieval.embedding import SentenceTransformerEmbedder
from tekijin.retrieval.indexing import embed_corpus


def positive_int(value: str) -> int:
    """argparse type: accept only positive integers (rejected before any DB work)."""

    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        dest="only_missing",
        action="store_false",
        help="re-embed every row, not just those with a NULL embedding",
    )
    parser.add_argument("--batch-size", type=positive_int, default=64)
    parser.add_argument(
        "--no-e5-prefix",
        action="store_true",
        help="disable e5 query:/passage: prefixes (use for non-e5 models); "
        "overrides TEKIJIN_EMBEDDING_USE_E5_PREFIX",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()

    # CLI flag overrides the configured default (when the flag is absent, follow
    # settings, which itself follows TEKIJIN_EMBEDDING_USE_E5_PREFIX).
    use_e5_prefix = False if args.no_e5_prefix else settings.embedding_use_e5_prefix

    embedder = SentenceTransformerEmbedder(use_e5_prefix=use_e5_prefix)
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
