"""Apply non-destructive schema migrations WITHOUT reseeding.

Unlike ``python -m tekijin.data.seed`` (which TRUNCATES every table and reloads
the fixtures), this creates any missing tables and runs the idempotent additive
DDL only — so an existing database keeps its rows. Use it at deploy time when the
schema changed (e.g. the embedding dimension after a model swap, #63) but the
data must be retained.

NOTE: an embedding column widened by a dimension change is reset to ``NULL`` (a
model change invalidates old vectors); recompute them afterwards with
``make embed``.

CLI::

    python -m tekijin.data.migrate
"""

from __future__ import annotations

import sys

from tekijin.data.seed import apply_migrations


def main() -> int:  # pragma: no cover - thin CLI wrapper around apply_migrations
    apply_migrations()
    print("Applied TEKIJIN schema migrations (non-destructive; data retained).")
    print("If the embedding dimension changed, re-run `make embed`.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI
    sys.exit(main())
