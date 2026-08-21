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

from collections.abc import Iterable, Sequence
from typing import Any, Protocol


class Tokenizer(Protocol):
    """Segments text into a list of lexical tokens."""

    def tokenize(self, text: str) -> list[str]: ...  # pragma: no cover - protocol stub


class SudachiTokenizer:
    """SudachiPy tokenizer in split mode C (compound-preserving).

    The dictionary is built lazily on first use and reused thereafter.
    """

    def __init__(self) -> None:
        self._tokenizer: Any | None = None
        self._mode: Any | None = None

    def _ensure(self) -> Any:
        if self._tokenizer is None:
            from sudachipy import dictionary, tokenizer

            self._tokenizer = dictionary.Dictionary().create()
            self._mode = tokenizer.Tokenizer.SplitMode.C
        return self._tokenizer

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

    def __init__(self, ids: list[Any], bm25: Any | None, tokenizer: Tokenizer) -> None:
        self._ids = ids
        self._bm25 = bm25
        self._tokenizer = tokenizer

    @classmethod
    def build(
        cls,
        docs: Iterable[tuple[Any, str]],
        *,
        tokenizer: Tokenizer | None = None,
    ) -> BM25Index:
        """Tokenize and index ``docs`` (pairs of ``(id, text)``)."""

        tok = tokenizer or SudachiTokenizer()
        ids: list[Any] = []
        corpus: list[list[str]] = []
        for id_, text in docs:
            ids.append(id_)
            corpus.append(tok.tokenize(text or ""))

        bm25: Any | None = None
        if corpus:
            from rank_bm25 import BM25Okapi

            bm25 = BM25Okapi(corpus)
        return cls(ids, bm25, tok)

    def search(self, query: str, top_k: int = 10) -> list[tuple[Any, float]]:
        """Return the ``top_k`` highest-scoring ``(id, score)`` for ``query``.

        Only positive-scoring documents (those sharing at least one query term)
        are returned, so the result may be shorter than ``top_k`` — or empty when
        the index is empty or the query has no indexable tokens.
        """

        if self._bm25 is None:
            return []
        tokens = self._tokenizer.tokenize(query or "")
        if not tokens:
            return []
        scores: Sequence[float] = self._bm25.get_scores(tokens)
        ranked = sorted(
            zip(self._ids, scores, strict=True),
            key=lambda pair: (-float(pair[1]), str(pair[0])),
        )
        return [(id_, float(score)) for id_, score in ranked[:top_k] if score > 0]
