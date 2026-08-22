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
from tekijin.retrieval.fusion import rrf
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


def test_fuse_accepts_three_rankings() -> None:
    retriever = _retriever_without_db(top_k=5)
    # "x" is rank 1 in all three lists; agreement lifts it above each list's #0.
    fused = retriever._fused_ids(["a", "x"], ["b", "x"], ["c", "x"])
    assert fused[0] == "x"


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
            self, *, use_e5_prefix: bool, trust_remote_code: bool = True, revision=None
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
            self, *, use_e5_prefix: bool, trust_remote_code: bool = True, revision=None
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
