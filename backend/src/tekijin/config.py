"""Application configuration.

Settings are loaded from environment variables (prefix ``TEKIJIN_``) with
sensible defaults for local development. Use :func:`get_settings` to obtain a
cached singleton.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root, resolved relative to this file:
#   <repo>/backend/src/tekijin/config.py -> parents[3] == <repo>
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FIXTURES_DIR = _REPO_ROOT / "fixtures" / "synthetic"


class Settings(BaseSettings):
    """Runtime settings for the TEKIJIN backend.

    Field values are read from ``TEKIJIN_*`` environment variables when present,
    otherwise the defaults below are used.
    """

    model_config = SettingsConfigDict(
        env_prefix="TEKIJIN_",
        # Absolute path to the repository-root .env (the one .env.example targets),
        # so it is read consistently regardless of the process working directory.
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,  # immutable singleton: mutation must not leak across requests
    )

    app_env: str = "development"

    # Database (connection code lands in a later component).
    database_url: str = "postgresql+psycopg://tekijin:tekijin@localhost:5432/tekijin"

    # LLM serving via vLLM (OpenAI-compatible /v1).
    llm_base_url: str = "http://internship-dgx1:8080/v1"
    llm_model: str = "Qwen3.6-35B-A3B-NVFP4"
    llm_api_key: str = "dummy"

    # Whether to let a reasoning model (Qwen3) emit its <think> chain before the
    # answer. Kept OFF by default: with thinking ON the C1/C2 structured-output
    # (tool-call) calls became both slow (C1+C2 p50 ≈ 83s) AND unreliable — the
    # reasoning pass sometimes suppressed the forced tool call, so
    # ``with_structured_output`` returned ``None`` and routing crashed (#140).
    # Passed to vLLM as ``chat_template_kwargs={"enable_thinking": ...}``; the
    # Qwen3 chat template reads it. This is a SINGLE GLOBAL switch shared by all
    # LLM nodes (C1 intent, C2 sufficiency, AND C7 draft) — flipping it on to
    # experiment with draft (C7) quality also re-enables thinking on C1/C2 and can
    # reintroduce the structured-output breakage, so change it deliberately.
    llm_enable_thinking: bool = False

    # Which C1/C2/C7 implementation the API wires: "stub" = deterministic,
    # network-free defaults (CI/tests); "vllm" = real langchain-openai client
    # against ``llm_base_url``. A ``Literal`` so an invalid value is rejected at
    # startup (never a silent fallback). Default stub keeps imports/CI LLM-free.
    llm_backend: Literal["stub", "vllm"] = "stub"

    # LangGraph checkpointer for session persistence / interrupt-resume:
    # "memory" = in-process MemorySaver (safe default, works without a DB);
    # "postgres" = PostgresSaver over ``database_url`` (production). A ``Literal``
    # so a typo is rejected at startup; a valid "postgres" that cannot be set up
    # still falls back to MemorySaver at runtime (see the checkpointer factory).
    checkpointer_backend: Literal["memory", "postgres"] = "memory"

    # Whether to FAIL-CLOSED on durability (#180): refuse to start on a non-durable
    # checkpointer (``memory``, or a ``postgres`` whose setup fails) instead of
    # silently degrading to in-memory — which would drop every session on the next
    # restart. ``None`` (default) derives from ``app_env`` (enforced when it is not
    # "development"). It is a SEPARATE knob from ``app_env`` on purpose: the DGX host
    # must run ``app_env=development`` for an unrelated reason (#108/#173 — the local
    # embedding model has no pinnable revision), so tying durability to ``app_env``
    # alone would leave it OFF exactly where it matters. Set
    # ``TEKIJIN_STRICT_DURABILITY=true`` on such a host to enforce it regardless, or
    # ``false`` as an explicit escape hatch in a throwaway prod-flavored env.
    strict_durability: bool | None = None

    def durability_enforced(self) -> bool:
        """True when a non-durable checkpointer must be a hard error, not a fallback."""

        if self.strict_durability is not None:
            return self.strict_durability
        return self.app_env != "development"

    # Embedding model used by the retrieval component (C3). Chosen by the on-DGX
    # benchmark (#61): Nemotron-3-Embed-1B was 1st of 5 on the primary metric
    # (層2 Recall@3 0.615 vs e5-large 0.530). See docs/benchmarks/README.md.
    embedding_model: str = "nvidia/Nemotron-3-Embed-1B-BF16"

    # Whether to prepend e5-style ``query:`` / ``passage:`` prefixes when
    # embedding. Correct for the e5 family AND Nemotron-3-Embed (both expect
    # ``query: `` / ``passage: ``); other models (e.g. ruri, BGE, Qwen3-Embedding
    # instruction-style) must NOT receive these prefixes or retrieval quality
    # degrades. Kept configurable because the embedding model is still being
    # benchmarked — flip this off when switching to a non-prefix model.
    embedding_use_e5_prefix: bool = True

    # Per-kind instruction prefixes, overriding the e5 ``query:``/``passage:`` pair
    # for that kind. Needed to reproduce an instruction-tuned fallback model's
    # benchmarked retrieval setup: the #61 Qwen3-Embedding bench prefixes QUERIES
    # with ``Instruct: <task>\nQuery: `` and PASSAGES with nothing (see
    # scripts/bench_embeddings.py). ``None`` (default) falls back to
    # ``embedding_use_e5_prefix`` for that kind; an empty string is a MEANINGFUL
    # override (= no prefix), distinct from ``None``. Set both when switching to
    # Qwen so index-time and query-time prefixes match the bench (#108).
    embedding_query_prefix: str | None = None
    embedding_passage_prefix: str | None = None

    # Whether to allow the embedding model to execute its own (remote) modeling
    # code at load time (``SentenceTransformer(..., trust_remote_code=True)``).
    # REQUIRED by the default Nemotron-3-Embed-1B (it ships custom modeling code);
    # without it ``make embed`` fails during model loading. SECURITY: this runs
    # code from the model repo, so keep it True only for a trusted, pinned default
    # and set it False when pointing ``embedding_model`` at an untrusted source.
    embedding_trust_remote_code: bool = True

    # Immutable model revision (git commit SHA or tag on the HF repo) to load.
    # SECURITY: with ``trust_remote_code=True``, loading the mutable default branch
    # means an upstream change/compromise would execute new code on the next cold
    # load. Pin a reviewed revision here for any real deployment so the executed
    # code is fixed. ``None`` loads the default branch (fine for local/dev).
    embedding_model_revision: str | None = None

    # Dimensionality of the embedding vectors produced by ``embedding_model``.
    # Nemotron-3-Embed-1B emits 2048-d vectors; this drives the width of every
    # ``pgvector`` column so the schema and the model agree. NOTE: the columns
    # stay ``vector`` (not ``halfvec``): ``vector`` stores up to 16000 dims and
    # the retrieval is brute-force, so 2048 is fine everywhere (incl. pgvector
    # 0.6.2). ``halfvec`` is only needed once an HNSW/ivfflat ANN index is added
    # (``vector`` indexes cap at 2000 dims) — deferred to #101.
    embedding_dim: int = 2048

    # RRF weight for the BM25 (sparse) channel in C4 hybrid search; the dense
    # channels stay at 1.0. Equal-weight RRF cost -0.170 層2 R@3 on eval_person v2
    # (queries are symptom-worded, so lexical BM25 ranks are noisy — #68 /
    # docs/benchmarks/ablation.md §3). Down-weighting BM25 to ~0.2 recovers dense
    # -only accuracy. A fixed low weight under-serves product-name / model-number /
    # error-code queries (where BM25 is the only signal); making it adaptive to the
    # dense signal strength is tracked in #114. 0.0 disables BM25 entirely.
    bm25_weight: float = 0.2

    # Directory holding synthetic fixtures used for development/testing.
    fixtures_dir: Path = _DEFAULT_FIXTURES_DIR

    # CORS allowed origins. Explicit (wildcard "*" + credentials is rejected by
    # browsers) and an immutable tuple so the cached singleton cannot be mutated.
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)


@lru_cache
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton."""
    return Settings()
