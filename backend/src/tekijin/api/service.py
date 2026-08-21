"""Application service that drives the C1-C8 agent behind the HTTP API.

One :class:`AgentService` is created per process and shared across requests. It
holds the long-lived pieces (checkpointer, embedder, LLM nodes, sessionmaker) and
builds a *fresh* graph per stream (bound to a fresh DB session), so no SQLAlchemy
session is shared across requests. Interrupt/resume is carried entirely by the
shared checkpointer keyed on ``thread_id == session_id`` — a graph rebuilt for a
later request resumes the earlier paused state.

Flow: ``/ask`` (or ``/answer``) enqueues the next input for a session; ``/events``
dequeues it and streams the run, mapping node updates to SSE events until the run
ends or pauses at an ``interrupt``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from langgraph.types import Command
from sqlalchemy.orm import Session, sessionmaker
from sse_starlette import ServerSentEvent

from tekijin.agent.graph import build_agent
from tekijin.agent.protocols import DraftModel, IntentModel, SufficiencyModel
from tekijin.agent.state import AgentState
from tekijin.api.events import interrupt_event, node_event
from tekijin.retrieval.embedding import Embedder


def _default_now() -> dt.datetime:
    # Naive (no tzinfo) to match stored ``created_at`` and the scorer's contract.
    return dt.datetime.now()  # noqa: DTZ005 - naive is intentional


def _interrupt_payload(value: Any) -> dict[str, Any]:
    # stream yields {"__interrupt__": (Interrupt(...),)}; take the first payload.
    if value and isinstance(value, tuple):
        return getattr(value[0], "value", {}) or {}
    return {}


class AgentService:
    """Builds graphs, tracks per-session pending input, and streams SSE events."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        checkpointer: Any,
        embedder: Embedder,
        intent_model: IntentModel,
        sufficiency_model: SufficiencyModel,
        draft_model: DraftModel,
        retriever: Any | None = None,
        scorer: Any | None = None,
        now_factory: Any = _default_now,
    ) -> None:
        self._session_factory = session_factory
        self._checkpointer = checkpointer
        self._embedder = embedder
        self._intent = intent_model
        self._sufficiency = sufficiency_model
        self._draft = draft_model
        # Optional C4/C6 overrides — default (None) uses the real HybridRetriever
        # / ExpertiseScorer over the request session; tests inject deterministic
        # fakes so the SSE flow does not depend on retrieval scores.
        self._retriever = retriever
        self._scorer = scorer
        self._now_factory = now_factory
        self._pending: dict[str, Any] = {}

    # -- session access for the dashboard --------------------------------- #
    @property
    def session_factory(self) -> sessionmaker[Session]:
        return self._session_factory

    # -- pending-input registry ------------------------------------------- #
    def enqueue_question(self, session_id: str, asker_id: int, question: str) -> None:
        """Register a NEW question (fresh invoke) for ``session_id``."""

        state: AgentState = {
            "question": question,
            "asker": {"id": asker_id},
            "now": self._now_factory(),
        }
        self._pending[session_id] = state

    def enqueue_resume(self, session_id: str, resume_value: str) -> None:
        """Register a RESUME (Command) for a paused ``session_id``."""

        self._pending[session_id] = Command(resume=resume_value)

    def has_pending(self, session_id: str) -> bool:
        return session_id in self._pending

    # -- streaming -------------------------------------------------------- #
    def _graph(self, session: Session) -> Any:
        return build_agent(
            self._embedder,
            session,
            checkpointer=self._checkpointer,
            intent_model=self._intent,
            sufficiency_model=self._sufficiency,
            draft_model=self._draft,
            retriever=self._retriever,
            scorer=self._scorer,
        )

    def stream_events(self, session_id: str) -> Iterator[ServerSentEvent]:
        """Stream the queued run for ``session_id`` as SSE events.

        Consumes the pending input (raising ``KeyError`` if none). A fresh DB
        session is opened for the run and always closed; on an unexpected error an
        ``error`` event is emitted and the stream ends cleanly.
        """

        agent_input = self._pending.pop(session_id)
        config = {"configurable": {"thread_id": session_id}}
        session = self._session_factory()
        try:
            graph = self._graph(session)
            for update in graph.stream(agent_input, config, stream_mode="updates"):
                for node, data in update.items():
                    event = (
                        interrupt_event(_interrupt_payload(data))
                        if node == "__interrupt__"
                        else node_event(node, data)
                    )
                    if event is not None:
                        yield event
        except Exception as exc:  # pragma: no cover - defensive; surfaced as an event
            from tekijin.api import schemas

            yield ServerSentEvent(
                event="error", data=schemas.ErrorData(error=str(exc)).model_dump_json()
            )
        finally:
            session.close()
