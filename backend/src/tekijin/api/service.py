"""Application service that drives the C1-C8 agent behind the HTTP API.

One :class:`AgentService` is created per process and shared across requests. It
holds the long-lived pieces (checkpointer, embedder, LLM nodes, sessionmaker) and
builds a *fresh* graph per stream (bound to a fresh DB session), so no SQLAlchemy
session is shared across requests. Interrupt/resume is carried by the shared
checkpointer keyed on ``thread_id == session_id`` — a graph rebuilt for a later
request resumes the earlier paused state.

Whether a session is "mid-interrupt" (awaiting a resume) is derived from the
DURABLE state — ``graph.get_state(config).next`` — not from an in-memory flag, so
the truth survives process boundaries. The small in-memory registry only holds
the *next input to run* plus per-session persistence ids (question / current
recommendation); it is TTL-evicted and is the reason the API MUST run single
worker (documented in the Makefile ``serve`` target).

Flow: ``/ask`` (new question) and ``/answer`` (resume) enqueue the next input;
``/events`` streams it — or, when nothing is queued but the session is paused,
re-emits the pending interrupt so a client can reconnect. Questions and C6
recommendations are persisted; ``/answer`` records the outcome.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command
from sqlalchemy.orm import Session, sessionmaker
from sse_starlette import ServerSentEvent

from tekijin.agent.graph import build_agent
from tekijin.agent.protocols import DraftModel, IntentModel, SufficiencyModel
from tekijin.api import schemas
from tekijin.api.events import interrupt_event, node_event, reconnect_event
from tekijin.data.db import session_scope
from tekijin.data.writes import (
    insert_recommendation,
    persist_question,
    set_recommendation_outcome,
    update_question_topics,
)
from tekijin.retrieval.embedding import Embedder

logger = logging.getLogger(__name__)

# In-memory session entries older than this (no activity) are evicted so an /ask
# that is never followed by /events cannot leak.
SESSION_TTL_SECONDS = 3600.0


class SessionConflict(Exception):
    """The request conflicts with the session's state (maps to HTTP 409)."""


class SessionInvalid(Exception):
    """The resume kind does not match the pending interrupt (maps to HTTP 422)."""


def _default_now() -> dt.datetime:
    # Naive (no tzinfo) to match stored ``created_at`` and the scorer's contract.
    return dt.datetime.now()  # noqa: DTZ005 - naive is intentional


def _interrupt_payload(value: Any) -> dict[str, Any]:
    # stream yields {"__interrupt__": (Interrupt(...),)}; take the first payload.
    if value and isinstance(value, tuple):
        return getattr(value[0], "value", {}) or {}
    return {}


def _error_event() -> ServerSentEvent:
    # Generic client-facing error — never leaks exception / SQL / model details.
    return ServerSentEvent(
        event="error",
        data=schemas.ErrorData(error="内部エラーが発生しました").model_dump_json(),
    )


@dataclass
class _SessionCtx:
    """Per-session in-memory bookkeeping (not the source of interrupt truth)."""

    pending: Any = None  # next input to stream (AgentState or Command), or None
    question_id: str | None = None
    current_rec_id: int | None = None
    now: dt.datetime | None = None
    ask_count: int = 0
    touched_at: float = field(default_factory=time.monotonic)


class AgentService:
    """Builds graphs, tracks per-session dispatch state, and streams SSE events."""

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
        self._registry: dict[str, _SessionCtx] = {}

    # -- session access for the dashboard --------------------------------- #
    @property
    def session_factory(self) -> sessionmaker[Session]:
        return self._session_factory

    def close(self) -> None:
        """Release long-lived resources at shutdown (checkpointer pool, engine)."""

        pool = getattr(self._checkpointer, "conn", None)  # PostgresSaver holds a pool
        closer = getattr(pool, "close", None)
        if callable(closer):
            closer()
        engine = self._session_factory.kw.get("bind")
        if engine is not None:
            engine.dispose()

    # -- registry / TTL --------------------------------------------------- #
    def _sweep(self) -> None:
        cutoff = time.monotonic() - SESSION_TTL_SECONDS
        stale = [sid for sid, ctx in self._registry.items() if ctx.touched_at < cutoff]
        for sid in stale:
            del self._registry[sid]

    def _config(self, session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

    def _next_nodes(self, session_id: str) -> tuple[str, ...]:
        """Durable interrupt state: the nodes the graph is paused before (if any)."""

        session = self._session_factory()
        try:
            state = self._graph(session).get_state(self._config(session_id))
            return tuple(state.next)
        finally:
            session.close()

    # -- /ask : start a new question -------------------------------------- #
    def start_question(self, session_id: str, asker_id: int, question: str) -> None:
        """Queue a NEW question; reject (409) if the session is busy or paused."""

        self._sweep()
        ctx = self._registry.get(session_id)
        if ctx is not None and ctx.pending is not None:
            raise SessionConflict("a run is already queued for this session")
        if self._next_nodes(session_id):
            raise SessionConflict("session is awaiting a resume; answer it first")

        now = self._now_factory()
        ctx = self._registry.setdefault(session_id, _SessionCtx())
        ctx.ask_count += 1
        question_id = f"api_{session_id}_{ctx.ask_count}"
        with session_scope(self._session_factory) as session:
            persist_question(session, question_id, asker_id, question, now)
        ctx.question_id = question_id
        ctx.current_rec_id = None
        ctx.now = now
        ctx.pending = {"question": question, "asker": {"id": asker_id}, "now": now}
        ctx.touched_at = time.monotonic()

    # -- /answer : resume a paused run ------------------------------------ #
    def submit_resume(
        self, session_id: str, *, outcome: str | None = None, reply: str | None = None
    ) -> None:
        """Queue a resume, validating it matches the pending interrupt kind."""

        next_nodes = self._next_nodes(session_id)
        if not next_nodes:
            raise SessionConflict("session is not awaiting a resume")
        node = next_nodes[0]
        if node == "ask":
            if reply is None:
                raise SessionInvalid("this session expects a clarification 'reply'")
            resume_value = reply
        elif node == "send":
            if outcome is None:
                raise SessionInvalid("this session expects a responder 'outcome'")
            resume_value = outcome
            ctx = self._registry.get(session_id)
            if ctx is not None and ctx.current_rec_id is not None:
                with session_scope(self._session_factory) as session:
                    set_recommendation_outcome(session, ctx.current_rec_id, outcome)
        else:  # pragma: no cover - the graph only ever interrupts at ask/send
            raise SessionConflict("session cannot be resumed from its current state")

        ctx = self._registry.setdefault(session_id, _SessionCtx())
        ctx.pending = Command(resume=resume_value)
        ctx.touched_at = time.monotonic()

    # -- /events : stream ------------------------------------------------- #
    def is_streamable(self, session_id: str) -> bool:
        """True if there is a queued run OR a paused run to reconnect to."""

        ctx = self._registry.get(session_id)
        if ctx is not None and ctx.pending is not None:
            return True
        return bool(self._next_nodes(session_id))

    def stream_events(self, session_id: str) -> Iterator[ServerSentEvent]:
        """Stream the queued run, or re-emit the pending interrupt on reconnect.

        A fresh DB session is opened for the run and always closed; everything is
        inside try/except so any failure closes cleanly with one generic ``error``
        event (details are logged server-side, never sent to the client).
        """

        session = self._session_factory()
        try:
            graph = self._graph(session)
            config = self._config(session_id)
            ctx = self._registry.get(session_id)
            if ctx is not None and ctx.pending is not None:
                pending = ctx.pending
                ctx.pending = None
                ctx.touched_at = time.monotonic()
                yield from self._run(graph, config, pending, ctx)
            else:
                state = graph.get_state(config)
                if state.next:
                    reconnect = reconnect_event(state.next[0], state.values)
                    if reconnect is not None:
                        yield reconnect
        except Exception:
            logger.exception("SSE stream failed for session %s", session_id)
            yield _error_event()
        finally:
            session.close()

    # -- internals -------------------------------------------------------- #
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

    def _run(
        self, graph: Any, config: dict[str, Any], agent_input: Any, ctx: _SessionCtx
    ) -> Iterator[ServerSentEvent]:
        for update in graph.stream(agent_input, config, stream_mode="updates"):
            for node, data in update.items():
                if node == "c1_intent":
                    self._persist_topics(ctx, data)
                elif node == "c6_score":
                    self._persist_recommendation(ctx, data)
                event = (
                    interrupt_event(_interrupt_payload(data))
                    if node == "__interrupt__"
                    else node_event(node, data)
                )
                if event is not None:
                    yield event

    def _persist_topics(self, ctx: _SessionCtx, data: dict[str, Any] | None) -> None:
        topics = (data or {}).get("topics") or []
        if ctx.question_id is not None:
            with session_scope(self._session_factory) as session:
                update_question_topics(session, ctx.question_id, topics)

    def _persist_recommendation(self, ctx: _SessionCtx, data: dict[str, Any] | None) -> None:
        recs = (data or {}).get("recommendations") or []
        if recs and ctx.question_id is not None and ctx.now is not None:
            top = recs[0]
            with session_scope(self._session_factory) as session:
                ctx.current_rec_id = insert_recommendation(
                    session,
                    ctx.question_id,
                    top["person_id"],
                    rank=1,
                    score=top.get("score"),
                    reasons=top.get("reasons") or [],
                    now=ctx.now,
                )
