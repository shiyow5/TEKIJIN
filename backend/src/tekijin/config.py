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

# INSECURE development defaults for auth (#241). Named so the fail-closed startup
# guard (``Settings.auth_enforced``) can detect "left at the default" without
# duplicating the literal — mirroring the ``strict_durability`` pattern.
DEV_AUTH_SECRET = "dev-insecure-change-me"
DEV_ADMIN_PASSWORD = "tekijin-admin"


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

    # Per-request timeout (seconds) for every LLM call (C1/C2/C7). A stuck vLLM
    # request otherwise hangs the run and holds a backpressure slot forever, so a
    # single GPU stall cascades into unavailability (#180 task 4). On timeout the
    # call raises and the run surfaces a graceful error event, freeing the slot.
    # ``None`` disables the bound (langchain's own default). The real Claude
    # fallback on timeout is a separate, credential-gated follow-up.
    llm_timeout_seconds: float | None = 60.0

    # Sampling temperature for the STRUCTURED calls (C1 intent / C2 sufficiency).
    # Default 0.0 so they are deterministic, as model-definition.md specifies
    # ("C1・C2 は低温"). ChatOpenAI otherwise sends its own default (0.7), which
    # made C1 non-deterministic and contributed to routing noise (#116 原因3).
    llm_temperature: float = 0.0

    # Separate temperature for the C7 hand-off DRAFT (free text). model-definition.md
    # wants "C7 は中温（自然さ）" — a deterministic draft reads stilted — so C7 does
    # NOT share the C1/C2 low temperature above. Kept a distinct knob so tuning the
    # draft's naturalness never re-introduces non-determinism into routing.
    llm_draft_temperature: float = 0.5

    # Hard cap on output tokens per LLM call (C1/C2/C7). Bounds a runaway
    # generation so C1 returns a short (possibly truncated) result instead of
    # burning the whole context and failing with ``finish_reason=length`` (#116
    # 原因2). 1024 comfortably covers the structured C1/C2 tool calls and a C7
    # hand-off draft. ``None`` sends no limit (the server default applies).
    llm_max_tokens: int | None = 1024

    # Client-side retries per LLM call. Default 0 so ``llm_timeout_seconds`` is a
    # HARD per-request bound: ChatOpenAI defaults to 2 retries and retries on a
    # timeout, which would make the real worst-case stall ``timeout × 3`` (~185s)
    # and hold a backpressure slot ~3× longer than intended (#180 task 4 review).
    llm_max_retries: int = 0

    # Which C1/C2/C7 implementation the API wires: "stub" = deterministic,
    # network-free defaults (CI/tests); "vllm" = real langchain-openai client
    # against ``llm_base_url``. A ``Literal`` so an invalid value is rejected at
    # startup (never a silent fallback). Default stub keeps imports/CI LLM-free.
    llm_backend: Literal["stub", "vllm"] = "stub"

    # Evidence-sufficiency critic (#70): reject to a graceful "no in-house expert"
    # terminal when the critic's 0–100 answerable score is BELOW this threshold.
    # Externalised (not hard-coded) because the measured accept band is wide and
    # flat (30–70) on a tiny reject set (#65/#67 §6). ``answerability_enabled``
    # gates the whole step OFF by default until it is wired + verified on the eval
    # (this is the component-only slice; the graph does not call it yet).
    answerability_enabled: bool = False
    answerability_threshold: int = 40

    # Self-answer (#291): when the retrieved past Q&A / documents already hold the
    # answer, reply DIRECTLY with a cited answer instead of handing off to a person
    # (the product pivot — "the answer is not always a person"). OFF by default until
    # the composer is wired into the graph + verified on the recall-centric eval; this
    # is the component-only slice (the graph does not call it yet).
    self_answer_enabled: bool = False

    # #327: corpus-count routing for prior_answer. Nemotron's answer cosine cannot
    # separate this route (PRIOR_ANSWER_SIM sits above the observed max — see
    # route.py / #119), so route on whether the top retrieved past answer is a
    # REUSED/canonical answer instead. ``None`` (default) = OFF: prior_answer stays
    # dormant and C5 behaves exactly as before. When set (e.g. 3), a top past
    # answer with ``reuse_count >=`` this value AND answer cosine >=
    # ``prior_answer_relevance_floor`` (a low noise floor, not a discriminator)
    # routes prior_answer. Calibrate the two on the DGX eval before enabling.
    prior_answer_reuse_min: int | None = None
    prior_answer_relevance_floor: float = 0.15

    # #355: include daily reports as C6 topic evidence. The eval gold derives from
    # projects + daily_reports(0.15), but the scorer was blind to daily reports —
    # so enriching daily activity (#326) could not lift R@3. This closes that
    # asymmetry. OFF by default (develop behaviour byte-identical); enable only
    # after DGX confirms a Pareto gain (primary R@3 up, alt not down).
    daily_evidence_enabled: bool = False

    # #357: knowledge framework. When the knowledge layer is wired into retrieval,
    # answer a question from structured knowledge units (problem → action → result,
    # with provenance) instead of, or before, pointing at a person. OFF by default —
    # this is the schema/CRUD skeleton slice; extraction, vector retrieval, and the
    # graph wiring land in later slices, and the graph does not call it yet. Enable
    # only after the extraction quality + A/B are verified on the eval.
    knowledge_retrieval_enabled: bool = False
    # #357 slice 4: the cosine-similarity floor a retrieved knowledge unit must clear
    # to be used in a knowledge answer. Nemotron cosines are compressed (~0.04–0.57),
    # so this is a moderate default to be CALIBRATED on the eval (slice 4b A/B) before
    # the knowledge answer is wired live. Inert while knowledge_retrieval_enabled=False.
    knowledge_answer_min_similarity: float = 0.35

    # LangGraph checkpointer for session persistence / interrupt-resume:
    # "memory" = in-process MemorySaver (safe default, works without a DB);
    # "postgres" = PostgresSaver over ``database_url`` (production). A ``Literal``
    # so a typo is rejected at startup; a valid "postgres" that cannot be set up
    # still falls back to MemorySaver at runtime (see the checkpointer factory).
    checkpointer_backend: Literal["memory", "postgres"] = "memory"

    # Backpressure (#180): a SOFT, best-effort admission gate — NOT a hard concurrency
    # ceiling. When this many graph runs are already executing, /ask sheds NEW
    # questions with HTTP 503 (a fast, graceful "混雑中"). Two caveats by design: it is
    # check-then-run (a burst of near-simultaneous /ask→/events can transiently
    # overshoot before any slot is taken), and it gates ONLY new questions — resumes /
    # continuations of paused runs are never shed. The HARD per-GPU bound is vLLM's own
    # ``max-num-seqs`` scheduler; this just keeps the app from accepting far more than
    # the model can batch. Defaults to 8 to match a typical ``max-num-seqs``. 0
    # disables the gate (fine for local/stub dev).
    max_concurrent_runs: int = 8

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

    # #114: make the BM25 RRF weight ADAPTIVE to the dense signal strength, to
    # reconcile the conflicting optima — symptom-worded eval queries want BM25 low
    # (``bm25_weight``), but product-name / model-number / error-code queries (where
    # the dense embedding is semantically uninformed and its top cosine is low) want
    # BM25 high or the exact match falls out of top-k (#68 warned of this).
    # ``bm25_weight_boosted`` is the weight when the dense channel is UNINFORMED
    # (its top cosine <= ``bm25_adapt_lo``); it decays linearly back to
    # ``bm25_weight`` as the dense confidence rises to ``bm25_adapt_hi``. ``None``
    # (default) keeps the FLAT ``bm25_weight`` everywhere — i.e. adaptivity is OFF.
    #
    # DGX tuning (2026-08-25, real Nemotron embeddings, scripts/research_bm25_sweep.py):
    # swept boosted ∈ {0.5,1.0,1.5} × window {0.15-0.35,0.10-0.25,0.20-0.45} on the
    # current eval. **boosted=0.5 / window 0.15-0.35 is the sweet spot** — layer-2
    # R@3 0.679→0.694 (+0.015), with-gold-topics 0.723→0.739, L2 0.708→0.736, no
    # L1/L3 regression, route accuracy unchanged (0.818). boosted=1.5 and a wider
    # window (0.20-0.45) regress; 0.10-0.25 is a no-op. So the DEFAULT window
    # (0.15-0.35) is already correct; the recommended boosted is 0.5.
    #
    # Still OFF by default: the gain is small and the PRIMARY target — product-name
    # / model-number queries — is not yet in the eval (that query set is #296's
    # remaining work), and the #70 misrejections (#37/#49) did NOT improve under any
    # config, so BM25 boosting alone is not their fix. Enable (set
    # ``TEKIJIN_BM25_WEIGHT_BOOSTED=0.5``) once the 型番/product-name eval confirms
    # the intended retrieval gain there. Re-run scripts/research_bm25_sweep.py to
    # re-tune when the corpus or embedding model changes.
    bm25_weight_boosted: float | None = None
    bm25_adapt_lo: float = 0.15
    bm25_adapt_hi: float = 0.35

    # Directory holding synthetic fixtures used for development/testing.
    fixtures_dir: Path = _DEFAULT_FIXTURES_DIR

    # CORS allowed origins. Explicit (wildcard "*" + credentials is rejected by
    # browsers) and an immutable tuple so the cached singleton cannot be mutated.
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    # --- Authentication (#241) ------------------------------------------------ #
    # HS256 signing secret for access tokens. The default is INSECURE (a fixed
    # dev value) — set TEKIJIN_AUTH_SECRET to a long random string in any real
    # deployment, or every issued token is forgeable. The startup guard
    # ``auth_enforced()`` refuses to boot on this default outside development.
    auth_secret: str = DEV_AUTH_SECRET

    # Access-token lifetime in hours (the "失効" of a stateless JWT). Logout is a
    # client-side token drop; there is no server session to revoke, so keep this
    # modest. 12h covers a demo/work session without a mid-use expiry.
    auth_token_ttl_hours: float = 12.0

    # The single ADMIN account. It is NOT a DB employee (the seeded roster stays
    # at 40 and the admin never becomes a recommendation candidate); it logs in
    # with these credentials and impersonates any employee via the demo switcher.
    # CHANGE admin_password in any real deployment (the default is a known value).
    admin_email: str = "admin@tekijin.local"
    admin_password: str = DEV_ADMIN_PASSWORD
    admin_name: str = "管理者"

    # Password seeded for EVERY employee (demo login). All synthetic, so a shared
    # demo password is acceptable and documented; override per environment.
    demo_user_password: str = "tekijin-demo"

    # Login brute-force throttle: at most this many FAILED attempts per email
    # within the rolling window before further attempts are refused (429).
    login_max_attempts: int = 5
    login_window_seconds: float = 300.0

    # Feedback flood throttle (#263): at most this many POST /feedback per actor
    # within the rolling window before further posts are refused (429). Feedback is
    # an append-only learning signal, so this bounds metric/learning-signal spam.
    feedback_max_per_window: int = 60
    feedback_window_seconds: float = 60.0

    # Fail-closed on insecure auth defaults (#241), mirroring ``strict_durability``.
    # ``None`` (default) derives from ``app_env`` (enforced when not "development").
    # A SEPARATE knob because the DGX host runs app_env=development for an unrelated
    # reason (#108/#173), so set TEKIJIN_STRICT_AUTH=true there to still refuse a
    # boot with the known default secret/admin password; or =false as an explicit
    # escape hatch in a throwaway prod-flavored env.
    strict_auth: bool | None = None

    def auth_enforced(self) -> bool:
        """True when insecure default auth secrets must be a hard startup error."""

        if self.strict_auth is not None:
            return self.strict_auth
        return self.app_env != "development"


@lru_cache
def get_settings() -> Settings:
    """Return the cached :class:`Settings` singleton."""
    return Settings()
