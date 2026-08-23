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
    intent_model, sufficiency_model, draft_model = make_llm_nodes(settings)
    embedder = SentenceTransformerEmbedder(
        settings.embedding_model,
        # All settings-driven from THIS instance (not the cached global), so a
        # custom/hardened Settings passed to build_default_service is honored.
        use_e5_prefix=settings.embedding_use_e5_prefix,
        query_prefix=settings.embedding_query_prefix,
        passage_prefix=settings.embedding_passage_prefix,
        trust_remote_code=settings.embedding_trust_remote_code,
        revision=settings.embedding_model_revision,
    )
    return AgentService(
        session_factory=session_factory,
        checkpointer=make_checkpointer(settings),
        embedder=embedder,
        intent_model=intent_model,
        sufficiency_model=sufficiency_model,
        draft_model=draft_model,
        # From THIS settings instance (not the cached global) so a custom Settings
        # is honored when the graph builds its C4 retriever (#68).
        bm25_weight=settings.bm25_weight,
    )
