"""Database-free unit tests for the retrieval layer.

Covers RRF fusion (pure), BM25 sparse search over real SudachiPy tokenization,
and the SentenceTransformerEmbedder prefix logic (with an injected fake model,
so no weights are downloaded).
"""

from __future__ import annotations

import pytest

from tekijin.retrieval.embedding import (
    PASSAGE,
    QUERY,
    Embedder,
    SentenceTransformerEmbedder,
)
from tekijin.retrieval.fusion import rrf
from tekijin.retrieval.sparse import BM25Index, SudachiTokenizer


# --------------------------------------------------------------------------- #
# fusion (pure)
# --------------------------------------------------------------------------- #
def test_rrf_rewards_agreement_across_lists() -> None:
    # "b" is ranked highly by both systems; it should win even though it is
    # never anyone's #1.
    dense = ["a", "b", "c"]
    sparse = ["b", "c", "a"]
    result = rrf([dense, sparse], k=60)
    ids = [id_ for id_, _ in result]
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


def test_rrf_known_scores() -> None:
    # Single list, k=60: score of rank-0 item is 1/(60+1).
    result = dict(rrf([["x", "y"]], k=60))
    assert result["x"] == pytest.approx(1 / 61)
    assert result["y"] == pytest.approx(1 / 62)


def test_rrf_sums_contributions_and_is_sorted() -> None:
    result = rrf([["a", "b"], ["a", "c"]], k=1)
    scores = dict(result)
    # "a" appears at rank 0 in both lists: 1/2 + 1/2 = 1.0.
    assert scores["a"] == pytest.approx(1.0)
    # Output is sorted by descending score.
    values = [s for _, s in result]
    assert values == sorted(values, reverse=True)
    assert result[0][0] == "a"


def test_rrf_deterministic_tiebreak_by_str() -> None:
    # Equal scores -> ordered by str(id); ints sort as strings deterministically.
    result = rrf([[3, 1, 2]], k=60)
    # ranks differ, so build a genuine tie: two singleton lists each rank-0.
    tie = rrf([[10], [2]], k=60)
    assert [id_ for id_, _ in tie] == [10, 2]  # "10" < "2" lexicographically
    assert [id_ for id_, _ in result] == [3, 1, 2]


def test_rrf_empty_input() -> None:
    assert rrf([]) == []
    assert rrf([[], []]) == []


def test_rrf_rejects_nonpositive_k() -> None:
    with pytest.raises(ValueError, match="k must be positive"):
        rrf([["a"]], k=0)


# --------------------------------------------------------------------------- #
# sparse / BM25 (real SudachiPy)
# --------------------------------------------------------------------------- #
_CORPUS = [
    ("d_vpn", "リモートアクセスVPNの設定手順とトラブルシューティング"),
    ("d_model", "RX-3000の見積を作成する方法"),
    ("d_jargon", "たよれーるの契約更新について社内で確認する"),
    ("d_other", "経費精算の締め切りは月末です"),
]


def test_bm25_matches_model_number() -> None:
    index = BM25Index.build(_CORPUS)
    hits = index.search("RX-3000 の見積", top_k=3)
    assert hits, "expected a lexical hit for the model number"
    assert hits[0][0] == "d_model"
    assert hits[0][1] > 0


def test_bm25_matches_inhouse_jargon() -> None:
    index = BM25Index.build(_CORPUS)
    hits = index.search("たよれーる 契約", top_k=3)
    assert hits[0][0] == "d_jargon"


def test_bm25_no_lexical_overlap_returns_empty() -> None:
    index = BM25Index.build(_CORPUS)
    # A query whose tokens appear in no document scores nothing positive.
    assert index.search("量子コンピュータ", top_k=3) == []


def test_bm25_empty_query_and_empty_index() -> None:
    index = BM25Index.build(_CORPUS)
    assert index.search("", top_k=3) == []
    empty = BM25Index.build([])
    assert empty.search("anything", top_k=3) == []


def test_bm25_top_k_limits_results() -> None:
    index = BM25Index.build(_CORPUS)
    # "の" is a common particle appearing in several docs; cap the hits at 1.
    hits = index.search("VPN の 設定 見積 契約", top_k=1)
    assert len(hits) <= 1


def test_sudachi_tokenizer_mode_c() -> None:
    tok = SudachiTokenizer()
    tokens = tok.tokenize("RX-3000の見積")
    assert "見積" in tokens
    # surfaces are lower-cased and whitespace is dropped
    assert tok.tokenize("   ") == []


# --------------------------------------------------------------------------- #
# embedding (injected fake model — no download)
# --------------------------------------------------------------------------- #
class _FakeModel:
    """Stand-in for a SentenceTransformer; records the texts it was given."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, texts, normalize_embeddings: bool = False):
        self.seen = list(texts)
        assert normalize_embeddings is True
        return [[float(len(t)), 0.5] for t in texts]


def test_embedder_applies_e5_query_prefix() -> None:
    model = _FakeModel()
    embedder = SentenceTransformerEmbedder(model=model)
    vecs = embedder.encode(["ネットワーク"], kind=QUERY)
    assert model.seen == ["query: ネットワーク"]
    assert vecs == [[float(len("query: ネットワーク")), 0.5]]


def test_embedder_applies_passage_prefix_by_default() -> None:
    model = _FakeModel()
    embedder = SentenceTransformerEmbedder(model=model)
    embedder.encode(["本文"])
    assert model.seen == ["passage: 本文"]


def test_embedder_can_disable_prefix() -> None:
    model = _FakeModel()
    embedder = SentenceTransformerEmbedder(model=model, use_e5_prefix=False)
    embedder.encode(["raw"], kind=PASSAGE)
    assert model.seen == ["raw"]


def test_embedder_rejects_unknown_kind() -> None:
    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    with pytest.raises(ValueError, match="kind must be one of"):
        embedder.encode(["x"], kind="document")


def test_sentence_transformer_embedder_is_an_embedder() -> None:
    # Structural typing: it satisfies the Embedder protocol.
    assert isinstance(SentenceTransformerEmbedder(model=_FakeModel()), Embedder)


# --- boundary validation (DB-free: raised before any DB access) ------------- #


@pytest.mark.parametrize(("top_k", "rrf_k"), [(0, 60), (-1, 60), (10, 0), (10, -5)])
def test_hybrid_retriever_rejects_nonpositive_params(top_k: int, rrf_k: int) -> None:
    from tekijin.retrieval.retriever import HybridRetriever

    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    with pytest.raises(ValueError, match="must be positive"):
        # session is never touched: validation runs first in __init__.
        HybridRetriever(embedder, session=None, top_k=top_k, rrf_k=rrf_k)  # type: ignore[arg-type]


@pytest.mark.parametrize("batch_size", [0, -1])
def test_embed_corpus_rejects_nonpositive_batch_size(batch_size: int) -> None:
    from tekijin.retrieval.indexing import embed_corpus

    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    with pytest.raises(ValueError, match="batch_size must be positive"):
        # session is never touched: validation runs before any query.
        embed_corpus(None, embedder, batch_size=batch_size)  # type: ignore[arg-type]
