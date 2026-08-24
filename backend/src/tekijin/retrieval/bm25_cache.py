"""Process-wide cache for the C4 BM25 indexes (#56).

``HybridRetriever`` is rebuilt per request, and its ``search()`` used to call
:meth:`BM25Index.build` three times (answers / documents / profiles) on **every**
query — each spinning up a SudachiPy dictionary (~30-40 ms) and re-tokenizing the
whole corpus, then discarding it. On the demo corpus that is pure waste: the
corpus changes only on writes.

This caches each built index keyed by a **content signature** and shares one
tokenizer (one dictionary load per process). A query on an unchanged corpus skips
tokenization + BM25 fit entirely; any content change (an id or text differs)
yields a new signature and rebuilds — so no explicit write-time invalidation is
needed, the cache self-invalidates on content.

The cache is process-local and guarded by a lock, so concurrent graph runs on
different sessions share it safely. It is intentionally small (three keys); the
signature comparison is exact (the materialized ``(id, text)`` pairs), never a
hash, so a collision can never serve a stale index.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any

from tekijin.retrieval.sparse import BM25Index, SudachiTokenizer

# One dictionary load per process, shared across every rebuild (the dictionary is
# built lazily inside the tokenizer on first ``tokenize``; sharing it means later
# rebuilds reuse it instead of loading a fresh ~30-40 ms dictionary each time).
_TOKENIZER = SudachiTokenizer()

# kind -> (signature, index). ``signature`` is the exact materialized pair list.
_CACHE: dict[str, tuple[list[tuple[Any, str]], BM25Index]] = {}
_LOCK = threading.Lock()


def cached_bm25_index(kind: str, docs: Iterable[tuple[Any, str]]) -> BM25Index:
    """Return a BM25 index over ``docs`` for ``kind``, rebuilding only on change.

    ``kind`` is a stable slot name (``"answers"`` / ``"documents"`` /
    ``"profiles"``). ``docs`` are ``(id, text)`` pairs; materializing them is cheap
    (string concatenation, no tokenization). If the pairs are identical to the
    cached build for this slot, the cached index is returned unchanged — skipping
    the expensive SudachiPy tokenization and ``BM25Okapi`` fit.
    """

    pairs = [(id_, text or "") for id_, text in docs]
    with _LOCK:
        cached = _CACHE.get(kind)
        if cached is not None and cached[0] == pairs:
            return cached[1]
    # Build outside the lock (tokenization is the slow part; holding the lock across
    # it would serialize unrelated rebuilds). A concurrent duplicate build is
    # harmless — last writer wins with an identical index.
    index = BM25Index.build(pairs, tokenizer=_TOKENIZER)
    with _LOCK:
        _CACHE[kind] = (pairs, index)
    return index


def clear_bm25_cache() -> None:
    """Drop all cached indexes (test isolation / explicit reset)."""

    with _LOCK:
        _CACHE.clear()
