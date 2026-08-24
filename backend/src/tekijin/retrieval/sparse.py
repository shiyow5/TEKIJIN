"""Sparse (lexical) retrieval with BM25 over SudachiPy tokens.

Dense vectors miss exact-match signals — model numbers, product names, and
in-house jargon ("RX-3000", "たよれーる", "SPR") — so those are recovered by
term-frequency search (technical-spec §3.4). Japanese has no whitespace word
boundaries, so text is first segmented with SudachiPy in split **mode C**, which
keeps compound words intact (good for jargon), then scored with
``rank_bm25.BM25Okapi``.

The index is in-memory: the demo corpus is a few hundred rows, so scoring every
document per query costs milliseconds and needs no separate search service.
SudachiPy and ``rank_bm25`` are imported lazily so importing this module is cheap.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from typing import Any, Protocol


class Tokenizer(Protocol):
    """Segments text into a list of lexical tokens."""

    def tokenize(self, text: str) -> list[str]: ...  # pragma: no cover - protocol stub


# One SudachiPy dictionary per process (the ~30-40 ms cost), lazily loaded under a
# lock and shared thereafter. The dictionary is immutable and safe to share; the
# per-thread Tokenizer is created from it (see below).
_DICT_LOCK = threading.Lock()
_DICTIONARY: Any | None = None


def _shared_dictionary() -> Any:
    global _DICTIONARY
    if _DICTIONARY is None:
        with _DICT_LOCK:
            if _DICTIONARY is None:  # double-checked; the load is the expensive part
                from sudachipy import dictionary

                _DICTIONARY = dictionary.Dictionary()
    return _DICTIONARY


class SudachiTokenizer:
    """SudachiPy tokenizer in split mode C (compound-preserving).

    A SudachiPy ``Tokenizer`` is NOT safe to share across threads — concurrent
    ``tokenize`` on one instance raises ``RuntimeError: Already borrowed``. Since
    the C4 index cache (#56) shares one ``SudachiTokenizer`` across concurrent
    graph runs, each thread gets its OWN ``Tokenizer`` (via ``threading.local``)
    created from the single shared, immutable dictionary — so the expensive
    dictionary load still happens once per process while tokenization stays
    thread-safe.
    """

    def __init__(self) -> None:
        self._local = threading.local()
        self._mode: Any | None = None

    def _ensure(self) -> Any:
        tk = getattr(self._local, "tokenizer", None)
        if tk is None:
            from sudachipy import tokenizer

            tk = _shared_dictionary().create()
            self._local.tokenizer = tk
            # Mode is a constant enum; assigning the same value from several threads
            # is a benign, idempotent race.
            self._mode = tokenizer.Tokenizer.SplitMode.C
        return tk

    def tokenize(self, text: str) -> list[str]:
        tk = self._ensure()
        return [
            morpheme.surface().lower()
            for morpheme in tk.tokenize(text or "", self._mode)
            if morpheme.surface().strip()
        ]


class BM25Index:
    """An in-memory BM25 index over ``(id, text)`` documents.

    Build with :meth:`build`; query with :meth:`search`. Ids are opaque and
    returned verbatim.
    """

    def __init__(
        self,
        ids: list[Any],
        bm25: Any | None,
        tokenizer: Tokenizer,
        doc_tokens: list[set[str]] | None = None,
    ) -> None:
        self._ids = ids
        self._bm25 = bm25
        self._tokenizer = tokenizer
        # Per-document token *sets* — the match signal (see :meth:`search`).
        self._doc_tokens = doc_tokens if doc_tokens is not None else []

    @classmethod
    def build(
        cls,
        docs: Iterable[tuple[Any, str]],
        *,
        tokenizer: Tokenizer | None = None,
    ) -> BM25Index:
        """Tokenize and index ``docs`` (pairs of ``(id, text)``).

        Documents that tokenize to nothing (empty or punctuation-only text) are
        dropped, keeping ids and token lists aligned. If nothing indexable
        remains, an empty index is returned — ``BM25Okapi`` divides by the mean
        document length, so an all-empty corpus would raise ``ZeroDivisionError``.
        """

        tok = tokenizer or SudachiTokenizer()
        ids: list[Any] = []
        corpus: list[list[str]] = []
        for id_, text in docs:
            tokens = tok.tokenize(text or "")
            if not tokens:
                continue
            ids.append(id_)
            corpus.append(tokens)

        bm25: Any | None = None
        if corpus:
            from rank_bm25 import BM25Okapi

            bm25 = BM25Okapi(corpus)
        doc_tokens = [set(tokens) for tokens in corpus]
        return cls(ids, bm25, tok, doc_tokens)

    def search(self, query: str, top_k: int = 10) -> list[tuple[Any, float]]:
        """Return up to ``top_k`` ``(id, score)`` documents matching ``query``.

        A document *matches* when it shares at least one token with the query
        (lexical overlap). Matches are then ranked by descending BM25 score.

        Overlap — not ``score > 0`` — is the match signal on purpose: on a small
        or homogeneous corpus BM25Okapi's IDF can go zero or negative when a term
        occurs in many (or, with a single document, all) documents, dragging a
        genuine exact-match score to ``<= 0``. Filtering on score would then drop
        exactly the model-number / in-house-term hits this sparse channel exists
        to recover. Overlap keeps every real match regardless of IDF sign, while
        still excluding unrelated documents. Empty index or query -> ``[]``.
        """

        if self._bm25 is None:
            return []
        tokens = self._tokenizer.tokenize(query or "")
        if not tokens:
            return []
        query_tokens = set(tokens)
        scores: Sequence[float] = self._bm25.get_scores(tokens)
        matches = [
            (id_, float(score))
            for id_, score, doc_tokens in zip(self._ids, scores, self._doc_tokens, strict=True)
            if query_tokens & doc_tokens
        ]
        matches.sort(key=lambda pair: (-pair[1], str(pair[0])))
        return matches[:top_k]
