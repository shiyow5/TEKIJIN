"""Build the default production :class:`AgentService` from settings.

Wires the real pieces: a sessionmaker over the configured database, the selected
checkpointer (memory/postgres with fallback), the lazy SentenceTransformer
embedder (no model loaded until first ``encode``), and the selected LLM backend
(stub/vllm). All construction here is cheap and import-light — no DB connection,
no model download, no LangChain OpenAI import on the stub path.
"""

from __future__ import annotations

from tekijin.api.checkpointer import make_checkpointer
from tekijin.api.service import AgentService
from tekijin.config import Settings, get_settings
from tekijin.data.db import get_engine, get_sessionmaker
from tekijin.llm.factory import make_llm_nodes
from tekijin.retrieval.embedding import SentenceTransformerEmbedder


def build_default_service(settings: Settings | None = None) -> AgentService:
    settings = settings or get_settings()
    session_factory = get_sessionmaker(get_engine(settings.database_url))
    intent_model, sufficiency_model, draft_model, answerability_model, self_answer_model = (
        make_llm_nodes(settings)
    )
    embedder = SentenceTransformerEmbedder(
        settings.embedding_model,
        # All settings-driven from THIS instance (not the cached global), so a
        # custom/hardened Settings passed to build_default_service is honored.
        use_e5_prefix=settings.embedding_use_e5_prefix,
        query_prefix=settings.embedding_query_prefix,
        passage_prefix=settings.embedding_passage_prefix,
        trust_remote_code=settings.embedding_trust_remote_code,
        revision=settings.embedding_model_revision,
        # app_env too, so the fail-closed guard checks THIS instance's env rather
        # than the global singleton (#108) — otherwise a hardened production Settings
        # built here could be validated against a development global and bypassed.
        app_env=settings.app_env,
    )
    return AgentService(
        session_factory=session_factory,
        checkpointer=make_checkpointer(settings),
        embedder=embedder,
        intent_model=intent_model,
        sufficiency_model=sufficiency_model,
        draft_model=draft_model,
        # #70: wire the evidence-sufficiency critic ONLY when enabled; otherwise
        # pass None so the graph compiles the pre-#70 flow (no critique node).
        # Default OFF until it is verified on the DGX eval (part3).
        answerability_model=answerability_model if settings.answerability_enabled else None,
        answerability_threshold=settings.answerability_threshold,
        # #291: wire the self-answer composer ONLY when enabled; else None keeps the
        # pre-#291 data-derived routes (document terminal / hand-off). Default OFF
        # until verified on the recall-centric eval (part3).
        self_answer_model=self_answer_model if settings.self_answer_enabled else None,
        # From THIS settings instance (not the cached global) so a custom Settings
        # is honored when the graph builds its C4 retriever (#68).
        bm25_weight=settings.bm25_weight,
        # #327: corpus-count routing for prior_answer from THIS settings instance
        # (None = OFF keeps prior_answer dormant, C5 unchanged).
        prior_answer_reuse_min=settings.prior_answer_reuse_min,
        prior_answer_relevance_floor=settings.prior_answer_relevance_floor,
        # #355: daily reports as C6 evidence (False = dormant, develop unchanged).
        daily_evidence=settings.daily_evidence_enabled,
        # #357 slice 4c: wire the knowledge-answer step ONLY when enabled; else None
        # keeps the pre-#357 graph (no knowledge_answer node). Default OFF until the
        # knowledge corpus is populated + verified (slice 4b calibrated the floor).
        knowledge_answer_min_similarity=(
            settings.knowledge_answer_min_similarity
            if settings.knowledge_retrieval_enabled
            else None
        ),
        # Backpressure admission limit (#180) from THIS settings instance.
        max_concurrent_runs=settings.max_concurrent_runs,
    )
