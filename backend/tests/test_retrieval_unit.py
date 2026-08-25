"""Database-free unit tests for the retrieval layer.

Covers RRF fusion (pure), BM25 sparse search over real SudachiPy tokenization,
and the SentenceTransformerEmbedder prefix logic (with an injected fake model,
so no weights are downloaded).
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tekijin.agent.state import PastAnswer
from tekijin.config import Settings, get_settings
from tekijin.retrieval.embedding import (
    PASSAGE,
    QUERY,
    Embedder,
    SentenceTransformerEmbedder,
)
from tekijin.retrieval.fusion import adaptive_bm25_weight, rrf
from tekijin.retrieval.retriever import HybridRetriever
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


def test_rrf_weights_scale_each_ranking_contribution() -> None:
    # Two singleton lists, each rank-0. Weighting the second below the first makes
    # the first-list id outrank it despite the identical rank (#68).
    result = dict(rrf([["a"], ["b"]], k=60, weights=[1.0, 0.2]))
    assert result["a"] == pytest.approx(1.0 / 61)
    assert result["b"] == pytest.approx(0.2 / 61)
    assert result["a"] > result["b"]


def test_rrf_weight_zero_omits_a_ranking_entirely() -> None:
    # A zero-weighted ranking contributes nothing AND does not introduce its ids
    # (BM25 fully off must not leak sparse-only hits). "b" appears only in the
    # zero-weighted list, so it must be absent from the result.
    result = dict(rrf([["a"], ["b"]], k=60, weights=[1.0, 0.0]))
    assert "b" not in result
    assert result["a"] == pytest.approx(1.0 / 61)


def test_rrf_weights_none_matches_all_ones() -> None:
    rankings = [["a", "b"], ["b", "c"]]
    assert rrf(rankings) == rrf(rankings, weights=[1.0, 1.0])


def test_rrf_rejects_weight_length_mismatch() -> None:
    with pytest.raises(ValueError, match="weights length"):
        rrf([["a"], ["b"]], weights=[1.0])


def test_rrf_rejects_negative_weight() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        rrf([["a"]], weights=[-0.1])


def test_rrf_rejects_non_finite_weight() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        rrf([["a"]], weights=[float("nan")])
    with pytest.raises(ValueError, match="finite and non-negative"):
        rrf([["a"]], weights=[float("inf")])


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


# --- fix 4: all-empty corpus must not raise (BM25Okapi divides by mean length) #
def test_bm25_all_empty_corpus_returns_empty_index() -> None:
    index = BM25Index.build([("a", "   "), ("b", ""), ("c", "!!!")])
    assert index.search("anything", top_k=3) == []


# --- #56: content-signature cache reuses the built index on an unchanged corpus #
def test_bm25_cache_reuses_index_until_content_changes() -> None:
    from tekijin.retrieval.bm25_cache import cached_bm25_index, clear_bm25_cache

    clear_bm25_cache()
    try:
        docs = [("a", "RX-3000 の設定"), ("b", "たよれーる 導入")]
        first = cached_bm25_index("answers", iter(docs))
        # Identical corpus -> the SAME index object (no re-tokenization / re-fit).
        assert cached_bm25_index("answers", iter(docs)) is first
        # A content change (same ids, different text) -> a fresh index.
        changed = cached_bm25_index("answers", iter([("a", "RX-3000 の設定"), ("b", "別の内容")]))
        assert changed is not first
        # A different slot is cached independently, never colliding with "answers".
        assert cached_bm25_index("documents", iter(docs)) is not changed
        # Clearing forces a rebuild on the next call.
        clear_bm25_cache()
        assert cached_bm25_index("answers", iter(docs)) is not first
    finally:
        clear_bm25_cache()


def test_shared_tokenizer_is_thread_safe() -> None:
    """A single SudachiTokenizer shared across threads (as the #56 cache does) must
    not raise ``RuntimeError: Already borrowed`` — each thread gets its own
    Tokenizer from the shared dictionary."""
    import threading

    tok = SudachiTokenizer()
    tok.tokenize("初期化")  # warm the shared dictionary once
    texts = ["RX-3000の設定変更", "たよれーる導入の見積", "VPN機器の3拠点接続"] * 60
    errors: list[str] = []

    def worker() -> None:
        try:
            for t in texts:
                tok.tokenize(t)
        except Exception as exc:  # noqa: BLE001 - record any concurrency failure
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], f"concurrent tokenize raised: {errors[:3]}"


def test_bm25_cache_index_still_searches_correctly() -> None:
    from tekijin.retrieval.bm25_cache import cached_bm25_index, clear_bm25_cache

    clear_bm25_cache()
    try:
        index = cached_bm25_index("documents", iter([("d_model", "RX-3000 の見積")]))
        hits = index.search("RX-3000", top_k=3)
        assert hits and hits[0][0] == "d_model"
    finally:
        clear_bm25_cache()


def test_bm25_drops_empty_docs_keeping_alignment() -> None:
    # The empty doc is skipped, but the real doc stays correctly indexed.
    index = BM25Index.build([("empty", ""), ("real", "VPN 設定 の 手順")])
    hits = index.search("VPN", top_k=3)
    assert hits and hits[0][0] == "real"


# --- fix 5: exact-match must survive non-positive IDF (small/homogeneous data) #
def test_bm25_single_document_exact_match_survives_nonpositive_idf() -> None:
    # With one document the IDF of every term is <= 0, so BM25 scores the exact
    # match <= 0. Overlap-based matching must still return it.
    index = BM25Index.build([("only", "RX-3000 の 見積 手順")])
    hits = index.search("RX-3000", top_k=3)
    assert hits and hits[0][0] == "only"


def test_bm25_term_present_in_all_docs_still_matches() -> None:
    # A term in every document has IDF <= 0; overlap keeps the hits anyway.
    corpus = [("d1", "VPN 設定"), ("d2", "VPN 手順"), ("d3", "VPN 障害")]
    hits = BM25Index.build(corpus).search("VPN", top_k=5)
    assert {id_ for id_, _ in hits} == {"d1", "d2", "d3"}


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


def test_embedder_trust_and_revision_default_from_settings() -> None:
    # With no explicit args, the loader flags come from settings (Nemotron default
    # needs trust_remote_code; revision defaults to None = the repo default branch).
    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    assert embedder._trust_remote_code is get_settings().embedding_trust_remote_code
    assert embedder._revision == get_settings().embedding_model_revision


def test_embedder_honors_explicit_trust_and_revision() -> None:
    # Explicit args win over the global settings, so a hardened caller (e.g. the
    # service factory forwarding a custom Settings) is honored.
    embedder = SentenceTransformerEmbedder(
        model=_FakeModel(), trust_remote_code=False, revision="deadbeef"
    )
    assert embedder._trust_remote_code is False
    assert embedder._revision == "deadbeef"


def test_embedder_revision_sentinel_distinguishes_omitted_from_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With a pinned revision in the (global) settings, an OMITTED revision inherits
    # it, but an EXPLICIT None (a fallback model wanting the default branch) must be
    # preserved — not silently overridden by the global pin.
    import tekijin.retrieval.embedding as emb

    pinned = Settings(_env_file=None, embedding_model_revision="global-pin")  # type: ignore[call-arg]
    monkeypatch.setattr(emb, "get_settings", lambda: pinned)

    assert emb.SentenceTransformerEmbedder(model=_FakeModel())._revision == "global-pin"
    assert emb.SentenceTransformerEmbedder(model=_FakeModel(), revision=None)._revision is None
    got = emb.SentenceTransformerEmbedder(model=_FakeModel(), revision="local")._revision
    assert got == "local"


def test_sentence_transformer_embedder_is_an_embedder() -> None:
    # Structural typing: it satisfies the Embedder protocol.
    assert isinstance(SentenceTransformerEmbedder(model=_FakeModel()), Embedder)


# --- #108 part 2: per-kind instruction prefixes (Qwen reproduction) --------- #
def test_embedder_per_kind_instruction_prefix_overrides_e5() -> None:
    # An explicit per-kind prefix (e.g. Qwen's Instruct:...\nQuery: for queries,
    # nothing for passages) is used verbatim, overriding the e5 query:/passage: pair.
    model = _FakeModel()
    embedder = SentenceTransformerEmbedder(
        model=model,
        query_prefix="Instruct: タスク\nQuery: ",
        passage_prefix="",
    )
    embedder.encode(["相談"], kind=QUERY)
    assert model.seen == ["Instruct: タスク\nQuery: 相談"]
    embedder.encode(["文書"], kind=PASSAGE)
    assert model.seen == ["文書"]  # empty override = no prefix at all


def test_embedder_prefix_override_none_falls_back_to_e5_per_kind() -> None:
    # None for one kind falls back to the e5 default for THAT kind only; the other
    # kind still honors its explicit override.
    model = _FakeModel()
    embedder = SentenceTransformerEmbedder(model=model, passage_prefix="検索文書: ")
    embedder.encode(["q"], kind=QUERY)
    assert model.seen == ["query: q"]  # None -> e5 fallback
    embedder.encode(["p"], kind=PASSAGE)
    assert model.seen == ["検索文書: p"]  # explicit override


def test_embedder_prefix_override_wins_even_when_e5_disabled() -> None:
    # An explicit prefix applies regardless of use_e5_prefix; the un-overridden
    # kind gets no prefix (e5 disabled).
    model = _FakeModel()
    embedder = SentenceTransformerEmbedder(model=model, use_e5_prefix=False, query_prefix="Q: ")
    embedder.encode(["q"], kind=QUERY)
    assert model.seen == ["Q: q"]
    embedder.encode(["p"], kind=PASSAGE)
    assert model.seen == ["p"]


def test_embedder_prefixes_default_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Omitted prefixes inherit the global settings (so build_default_service /
    # embed_fixtures forwarding them, or an operator's env, takes effect).
    import tekijin.retrieval.embedding as emb

    configured = Settings(  # type: ignore[call-arg]
        _env_file=None,
        embedding_query_prefix="Instruct: t\nQuery: ",
        embedding_passage_prefix="",
    )
    monkeypatch.setattr(emb, "get_settings", lambda: configured)
    model = _FakeModel()
    embedder = emb.SentenceTransformerEmbedder(model=model)
    embedder.encode(["q"], kind=QUERY)
    assert model.seen == ["Instruct: t\nQuery: q"]
    embedder.encode(["p"], kind=PASSAGE)
    assert model.seen == ["p"]


# --- #108 part 1: fail-closed remote-code load outside development ----------- #
def test_embedder_fail_closed_in_production_without_pinned_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # trust_remote_code=True + revision=None outside development would execute the
    # model repo's MOVING default branch at load; refuse to construct.
    import tekijin.retrieval.embedding as emb

    prod = Settings(_env_file=None, app_env="production")  # type: ignore[call-arg]
    monkeypatch.setattr(emb, "get_settings", lambda: prod)
    with pytest.raises(ValueError, match="trust_remote_code"):
        emb.SentenceTransformerEmbedder(model=_FakeModel())


def test_embedder_fail_closed_skips_local_model_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # A LOCAL model dir has no remote revision to pin and no moving-branch risk, so
    # the #108 guard must not block production startup for it (#173).
    import tekijin.retrieval.embedding as emb

    local = str(tmp_path)  # an existing directory
    prod = Settings(  # type: ignore[call-arg]
        _env_file=None, app_env="production", embedding_model=local
    )
    monkeypatch.setattr(emb, "get_settings", lambda: prod)
    embedder = emb.SentenceTransformerEmbedder(model=_FakeModel())  # must NOT raise
    assert embedder._model_name == local


def test_is_local_model_path_distinguishes_paths_from_repo_ids() -> None:
    from tekijin.retrieval.embedding import _is_local_model_path

    assert _is_local_model_path("/home/team_a/models/Nemotron-3-Embed-1B-BF16") is True
    assert _is_local_model_path("./models/local") is True
    assert _is_local_model_path("~/models/local") is True
    # HF repo id: no path markers, does not exist on disk -> remote (guard applies).
    assert _is_local_model_path("nvidia/Nemotron-3-Embed-1B-BF16") is False


def test_embedder_production_allows_pinned_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tekijin.retrieval.embedding as emb

    prod = Settings(  # type: ignore[call-arg]
        _env_file=None, app_env="production", embedding_model_revision="deadbeef"
    )
    monkeypatch.setattr(emb, "get_settings", lambda: prod)
    # Does not raise; the pinned revision fixes the executed code.
    assert emb.SentenceTransformerEmbedder(model=_FakeModel())._revision == "deadbeef"


def test_embedder_production_allows_trust_remote_code_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tekijin.retrieval.embedding as emb

    prod = Settings(  # type: ignore[call-arg]
        _env_file=None, app_env="production", embedding_trust_remote_code=False
    )
    monkeypatch.setattr(emb, "get_settings", lambda: prod)
    # No remote code executed, so an unpinned revision is fine.
    assert emb.SentenceTransformerEmbedder(model=_FakeModel())._trust_remote_code is False


def test_embedder_fail_closed_uses_forwarded_app_env_not_global() -> None:
    # REGRESSION (#108 review HIGH): the guard must consult the app_env the CALLER
    # forwards (from its own Settings), not the global singleton. With the global at
    # the default "development", an explicit production app_env must still trip it —
    # otherwise a hardened custom Settings is silently bypassed.
    assert get_settings().app_env == "development", "test assumes the default global env"
    with pytest.raises(ValueError, match="trust_remote_code"):
        SentenceTransformerEmbedder(model=_FakeModel(), app_env="production")


def test_embedder_explicit_development_app_env_overrides_production_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Inverse: a production GLOBAL must not spuriously trip the guard when the caller
    # forwards a development app_env from its own (e.g. local tooling) Settings.
    import tekijin.retrieval.embedding as emb

    prod_global = Settings(_env_file=None, app_env="production")  # type: ignore[call-arg]
    monkeypatch.setattr(emb, "get_settings", lambda: prod_global)
    emb.SentenceTransformerEmbedder(model=_FakeModel(), app_env="development")  # must not raise


def test_embedder_development_allows_unpinned_remote_code() -> None:
    # The default (development) path is unchanged: convenient, no pin required.
    assert get_settings().app_env == "development", "test assumes the default development env"
    SentenceTransformerEmbedder(model=_FakeModel())  # must not raise


# --- boundary validation (DB-free: raised before any DB access) ------------- #


@pytest.mark.parametrize(("top_k", "rrf_k"), [(0, 60), (-1, 60), (10, 0), (10, -5)])
def test_hybrid_retriever_rejects_nonpositive_params(top_k: int, rrf_k: int) -> None:
    from tekijin.retrieval.retriever import HybridRetriever

    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    with pytest.raises(ValueError, match="must be positive"):
        # session is never touched: validation runs first in __init__.
        HybridRetriever(embedder, session=None, top_k=top_k, rrf_k=rrf_k)  # type: ignore[arg-type]


def test_hybrid_retriever_rejects_negative_bm25_weight() -> None:
    from tekijin.retrieval.retriever import HybridRetriever

    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    with pytest.raises(ValueError, match="bm25_weight must be non-negative"):
        HybridRetriever(embedder, session=None, bm25_weight=-0.1)  # type: ignore[arg-type]


def test_hybrid_retriever_bm25_weight_defaults_from_settings() -> None:
    from tekijin.retrieval.retriever import HybridRetriever

    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    # None -> settings; an explicit value wins.
    default = HybridRetriever(embedder, session=None)  # type: ignore[arg-type]
    assert default._bm25_weight == get_settings().bm25_weight
    explicit = HybridRetriever(embedder, session=None, bm25_weight=0.42)  # type: ignore[arg-type]
    assert explicit._bm25_weight == 0.42


@pytest.mark.parametrize("batch_size", [0, -1])
def test_embed_corpus_rejects_nonpositive_batch_size(batch_size: int) -> None:
    from tekijin.retrieval.indexing import embed_corpus

    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    with pytest.raises(ValueError, match="batch_size must be positive"):
        # session is never touched: validation runs before any query.
        embed_corpus(None, embedder, batch_size=batch_size)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# fix 1: answer index text combines the linked question body with the answer
# --------------------------------------------------------------------------- #
def test_answer_text_combines_question_and_answer() -> None:
    answer = SimpleNamespace(question_id="q1", body="answer body")
    text = HybridRetriever._answer_text(answer, {"q1": "question body"})
    assert text == "question body answer body"


def test_answer_text_falls_back_to_answer_when_no_question_body() -> None:
    # Unknown question id -> fall back to the answer body alone.
    unknown = SimpleNamespace(question_id="missing", body="just the answer")
    assert HybridRetriever._answer_text(unknown, {}) == "just the answer"
    # Question present but with a NULL body -> same fallback.
    null_body = SimpleNamespace(question_id="q1", body="a")
    assert HybridRetriever._answer_text(null_body, {"q1": None}) == "a"


# --------------------------------------------------------------------------- #
# fix A/E: question-matched answers form an INDEPENDENT dense ranking for RRF
# --------------------------------------------------------------------------- #
def _retriever_without_db(top_k: int = 10) -> HybridRetriever:
    # session is stored but never touched by the helpers under test here.
    embedder = SentenceTransformerEmbedder(model=_FakeModel())
    return HybridRetriever(embedder, session=None, top_k=top_k)  # type: ignore[arg-type]


def test_question_mapped_answer_ids_ranks_by_question_similarity() -> None:
    from tekijin.retrieval.retriever import HybridRetriever

    # Question dense hits (id, similarity), best-first.
    question_hits = [("q1", 1.0), ("q2", 0.9)]
    # q1 -> a2, a3 ; q2 -> a2 (duplicate: first occurrence via q1 wins).
    answers_by_question = {"q1": ["a2", "a3"], "q2": ["a2"]}
    ranked = HybridRetriever._question_mapped_answer_ids(question_hits, answers_by_question)
    # Ranked by the question order, de-duplicated — an independent ranking, NOT
    # appended behind any direct-answer pool.
    assert ranked == ["a2", "a3"]


def test_fuse_accepts_dense_rankings_and_sparse() -> None:
    retriever = _retriever_without_db(top_k=5)
    # "x" is rank 1 in all three lists; agreement lifts it above each list's #0.
    fused = retriever._fused_ids([["a", "x"], ["b", "x"]], ["c", "x"], dense_confidence=1.0)
    assert fused[0] == "x"


def test_fuse_downweights_the_bm25_channel() -> None:
    # A dense #0 and a sparse #0 tie on rank, but the sparse channel is weighted
    # below 1.0, so the dense hit must win (guards the #68 fix). With equal weight
    # (bm25_weight=1.0) they'd tie and str-order would decide instead.
    weighted = _retriever_without_db(top_k=5)
    weighted._bm25_weight = 0.2
    fused = weighted._fuse([["dense_hit"]], ["sparse_hit"], dense_confidence=1.0)
    scores = dict(fused)
    assert scores["dense_hit"] > scores["sparse_hit"]
    assert fused[0][0] == "dense_hit"


# --------------------------------------------------------------------------- #
# #114: adaptive BM25 weight (dense-weak queries let BM25 lead)
# --------------------------------------------------------------------------- #
def test_adaptive_bm25_weight_flat_when_boosted_none() -> None:
    # Default (boosted=None) is the pre-#114 flat weight at every confidence.
    for c in (0.0, 0.15, 0.25, 0.5, 1.0):
        assert adaptive_bm25_weight(c, base=0.2, boosted=None, lo=0.15, hi=0.35) == 0.2


def test_adaptive_bm25_weight_interpolates_monotonically() -> None:
    kw = {"base": 0.2, "boosted": 1.0, "lo": 0.15, "hi": 0.35}
    assert adaptive_bm25_weight(0.10, **kw) == 1.0  # dense uninformed -> boosted
    assert adaptive_bm25_weight(0.15, **kw) == 1.0  # at lo -> boosted
    assert adaptive_bm25_weight(0.35, **kw) == 0.2  # at hi -> base
    assert adaptive_bm25_weight(0.50, **kw) == 0.2  # dense confident -> base
    mid = adaptive_bm25_weight(0.25, **kw)  # halfway -> between base and boosted
    assert 0.2 < mid < 1.0 and mid == pytest.approx(0.6)
    # strictly decreasing across the window
    xs = [adaptive_bm25_weight(c, **kw) for c in (0.15, 0.20, 0.25, 0.30, 0.35)]
    assert all(a >= b for a, b in zip(xs, xs[1:])) and xs[0] > xs[-1]  # noqa: B905


def test_adaptive_bm25_weight_degenerate_window_is_flat() -> None:
    # hi <= lo (no window) or boosted <= base falls back to base, never inverts.
    assert adaptive_bm25_weight(0.1, base=0.2, boosted=1.0, lo=0.3, hi=0.3) == 0.2
    assert adaptive_bm25_weight(0.1, base=0.5, boosted=0.2, lo=0.15, hi=0.35) == 0.5


def test_fuse_boosts_bm25_when_dense_channel_is_weak() -> None:
    # A term/model-number query: dense is uninformed (low confidence) and points
    # elsewhere; BM25 alone holds the exact hit. With adaptivity ON, the weak-dense
    # confidence raises BM25 so the sparse-only hit is NOT dominated by the dense #0.
    r = _retriever_without_db(top_k=5)
    r._bm25_weight = 0.2
    r._bm25_boosted = 1.0
    r._bm25_adapt_lo, r._bm25_adapt_hi = 0.15, 0.35
    # dense strong (0.5): BM25 stays at base -> the dense hit wins (pre-#114).
    strong = dict(r._fuse([["dense_hit"]], ["sparse_hit"], dense_confidence=0.5))
    assert strong["dense_hit"] > strong["sparse_hit"]
    # dense weak (0.05): BM25 boosted to 1.0 -> the exact sparse hit ties/leads.
    weak = dict(r._fuse([["dense_hit"]], ["sparse_hit"], dense_confidence=0.05))
    assert weak["sparse_hit"] >= strong["sparse_hit"]  # BM25 contribution rose
    assert weak["sparse_hit"] == pytest.approx(weak["dense_hit"])  # equal weight -> tie


# --------------------------------------------------------------------------- #
# fix B: responders and profile matches are round-robin interleaved
# --------------------------------------------------------------------------- #
def _pa(responder_id: int | None) -> PastAnswer:
    """A minimal PastAnswer (only responder_id matters to _aggregate_people)."""

    return {"qa_id": "q", "score": 0.0, "responder_id": responder_id}


def test_aggregate_people_interleaves_responder_first() -> None:
    retriever = _retriever_without_db(top_k=6)
    past = [_pa(1), _pa(2), _pa(1)]  # distinct: 1, 2
    people = retriever._aggregate_people(past, [30, 31, 32])
    # responder, profile, responder, profile, profile
    assert people == [1, 30, 2, 31, 32]


def test_aggregate_people_profile_survives_many_responders() -> None:
    # 10 responders, default-ish small top_k: the profile hit must still appear.
    retriever = _retriever_without_db(top_k=3)
    past = [_pa(i) for i in range(1, 11)]
    people = retriever._aggregate_people(past, [99])
    assert people == [1, 99, 2]  # profile hit lands at position 1, inside top_k
    assert 99 in people


def test_aggregate_people_skips_none_responders() -> None:
    retriever = _retriever_without_db(top_k=5)
    past = [_pa(None), _pa(7)]
    people = retriever._aggregate_people(past, [7, 8])
    # None dropped; 7 de-duplicated across the two channels.
    assert people == [7, 8]


# --------------------------------------------------------------------------- #
# fix 3: e5 prefix toggle is a setting (default true, env-overridable)
# --------------------------------------------------------------------------- #
def test_embedding_use_e5_prefix_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("TEKIJIN_"):
            monkeypatch.delenv(key, raising=False)
    assert Settings(_env_file=None).embedding_use_e5_prefix is True


def test_embedding_use_e5_prefix_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEKIJIN_EMBEDDING_USE_E5_PREFIX", "false")
    assert Settings(_env_file=None).embedding_use_e5_prefix is False


def test_embedding_per_kind_prefix_defaults_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("TEKIJIN_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.embedding_query_prefix is None
    assert settings.embedding_passage_prefix is None


def test_embedding_per_kind_prefix_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEKIJIN_EMBEDDING_QUERY_PREFIX", "Instruct: t\nQuery: ")
    monkeypatch.setenv("TEKIJIN_EMBEDDING_PASSAGE_PREFIX", "")
    settings = Settings(_env_file=None)
    assert settings.embedding_query_prefix == "Instruct: t\nQuery: "
    assert settings.embedding_passage_prefix == ""


# --------------------------------------------------------------------------- #
# fix 7 + fix 3: embed CLI argument handling and prefix wiring
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_embed_script():
    spec = importlib.util.spec_from_file_location(
        "embed_fixtures_under_test", _REPO_ROOT / "scripts" / "embed_fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("bad", ["0", "-1", "-10"])
def test_embed_cli_positive_int_rejects_nonpositive(bad: str) -> None:
    import argparse

    mod = _load_embed_script()
    with pytest.raises(argparse.ArgumentTypeError, match="positive integer"):
        mod.positive_int(bad)


def test_embed_cli_positive_int_accepts_positive() -> None:
    mod = _load_embed_script()
    assert mod.positive_int("64") == 64


def test_embed_cli_parser_flags() -> None:
    mod = _load_embed_script()
    parser = mod.build_parser()
    default = parser.parse_args([])
    assert default.only_missing is True and default.no_e5_prefix is False
    flagged = parser.parse_args(["--all", "--no-e5-prefix", "--batch-size", "8"])
    assert flagged.only_missing is False
    assert flagged.no_e5_prefix is True
    assert flagged.batch_size == 8


@pytest.mark.parametrize(
    ("argv", "expected_prefix"),
    [(["--no-e5-prefix"], False), ([], True)],
)
def test_embed_cli_main_wires_prefix(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected_prefix: bool
) -> None:
    mod = _load_embed_script()
    from tekijin.config import get_settings

    dim = get_settings().embedding_dim
    captured: dict[str, bool] = {}

    class _CapturingEmbedder:
        def __init__(
            self, *, use_e5_prefix: bool, trust_remote_code: bool = True, revision=None, **_kw
        ) -> None:
            captured["prefix"] = use_e5_prefix

        def encode(self, texts, *, kind="passage"):
            # Probe (fix C): return a correct-width vector so the check passes.
            return [[0.0] * dim for _ in texts]

    @contextlib.contextmanager
    def _fake_scope(_factory):
        yield object()

    monkeypatch.setattr(mod, "SentenceTransformerEmbedder", _CapturingEmbedder)
    monkeypatch.setattr(mod, "get_engine", lambda: object())
    monkeypatch.setattr(mod, "get_sessionmaker", lambda _engine: lambda: object())
    monkeypatch.setattr(mod, "session_scope", _fake_scope)
    monkeypatch.setattr(
        mod,
        "embed_corpus",
        lambda _session, _embedder, *, only_missing, batch_size: {"answers": 0},
    )

    rc = mod.main(argv)
    assert rc == 0
    # Default (no flag) follows settings, whose default is True.
    assert captured["prefix"] is expected_prefix


def test_embed_cli_verify_embedding_width_ok() -> None:
    mod = _load_embed_script()
    # Matching widths -> no error.
    assert mod.verify_embedding_width(1024, 1024) is None


def test_embed_cli_verify_embedding_width_mismatch() -> None:
    mod = _load_embed_script()
    with pytest.raises(ValueError, match="requires rebuilding the pgvector schema"):
        mod.verify_embedding_width(768, 1024)


def test_embed_cli_main_rejects_dimension_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    # A model whose width differs from embedding_dim must fail BEFORE any DB call.
    mod = _load_embed_script()
    from tekijin.config import get_settings

    wrong_dim = get_settings().embedding_dim - 1

    class _WrongWidthEmbedder:
        def __init__(
            self, *, use_e5_prefix: bool, trust_remote_code: bool = True, revision=None, **_kw
        ) -> None:
            pass

        def encode(self, texts, *, kind="passage"):
            return [[0.0] * wrong_dim for _ in texts]

    def _boom(*_args, **_kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("DB must not be touched on a dimension mismatch")

    monkeypatch.setattr(mod, "SentenceTransformerEmbedder", _WrongWidthEmbedder)
    monkeypatch.setattr(mod, "get_engine", _boom)
    monkeypatch.setattr(mod, "get_sessionmaker", _boom)

    with pytest.raises(ValueError, match="Changing the vector dimension"):
        mod.main([])
