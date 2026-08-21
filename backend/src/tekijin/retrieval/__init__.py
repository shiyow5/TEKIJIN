"""Retrieval layer (Issue #29): C3 embeddings and C4 hybrid search.

Public surface for downstream components (#30 route selector, #31 agent):

* :class:`~tekijin.retrieval.retriever.HybridRetriever` — the C4 entry point.
* :class:`~tekijin.retrieval.embedding.Embedder` /
  :class:`~tekijin.retrieval.embedding.SentenceTransformerEmbedder` — C3.
* :func:`~tekijin.retrieval.indexing.embed_corpus` — index-time embedding writer.

Lower-level building blocks (:mod:`~tekijin.retrieval.dense`,
:mod:`~tekijin.retrieval.sparse`, :func:`~tekijin.retrieval.fusion.rrf`) are
imported from their modules directly.
"""

from __future__ import annotations

from tekijin.retrieval.embedding import Embedder, SentenceTransformerEmbedder
from tekijin.retrieval.fusion import rrf
from tekijin.retrieval.indexing import embed_corpus
from tekijin.retrieval.retriever import HybridRetriever
from tekijin.retrieval.sparse import BM25Index

__all__ = [
    "BM25Index",
    "Embedder",
    "HybridRetriever",
    "SentenceTransformerEmbedder",
    "embed_corpus",
    "rrf",
]
