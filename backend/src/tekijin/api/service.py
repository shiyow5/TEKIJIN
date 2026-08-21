"""Application service that drives the C1-C8 agent behind the HTTP API.

One :class:`AgentService` is created per process and shared across requests. It
holds the long-lived pieces (checkpointer, embedder, LLM nodes, sessionmaker) and
builds a *fresh* graph per stream (bound to a fresh DB session), so no SQLAlchemy
session is shared across requests. Interrupt/resume is carried by the shared
checkpointer keyed on ``thread_id == session_id`` — a graph rebuilt for a later
request resumes the earlier paused state.

Durability & concurrency (the core of this design):

* **Persistence identity lives in the durable checkpoint, not memory.** The
  ``question_id`` (a ``uuid4``) rides in the ``AgentState`` from ``/ask``; the DB
  ids of the shown recommendations — and the ``primary_recommendation_id`` whose
  outcome is later recorded — are written back into the state with
  ``graph.update_state`` after C6. So ``/answer`` records the outcome by reading
  ``graph.get_state(config).values`` and survives an eviction / restart. As a
  belt-and-suspenders fallback (e.g. a disconnect before ``update_state`` ran),
  the outcome target is re-derivable from the DB by ``question_id``.
* **A per-session lock makes accept, resume and stream mutually exclusive.** The
  in-memory registry shrinks to just "the next input to run" plus a TTL stamp;
  every state-transition (accept / resume / consume-and-stream) runs under the
  session's lock, so concurrent ``/ask`` cannot double-insert, concurrent
  ``/answer`` cannot drop an outcome, and concurrent ``/events`` cannot start the
  graph twice (double recommendation rows).
* **Eviction never drops a paused session.** ``_sweep`` skips any session that is
  mid-interrupt (a human is being waited on), so long HITL pauses are safe.

This still requires the API to run SINGLE worker (the lock + registry are
in-process); that is documented on the Makefile ``serve`` target.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
import uuid
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
    employee_exists,
    insert_shown_recommendations,
    latest_primary_recommendation,
    persist_question,
    set_recommendation_outcome,
    update_question_topics,
)
from tekijin.retrieval.embedding import Embedder

logger = logging.getLogger(__name__)

# In-memory session entries older than this (no activity) are evicted so an /ask
# that is never followed by /events cannot leak. Paused (mid-interrupt) sessions
# are exempt (see ``_sweep``).
SESSION_TTL_SECONDS = 3600.0

# The nodes the graph pauses *before* when it interrupts (HITL waits).
_INTERRUPT_NODES: frozenset[str] = frozenset({"ask", "send"})


class SessionConflict(Exception):
    """The request conflicts with the session's state (maps to HTTP 409)."""


class SessionInvalid(Exception):
    """The resume kind does not match the pending interrupt (maps to HTTP 422)."""


class AskerNotFound(Exception):
    """The ``asker_id`` is not a known employee (maps to HTTP 404)."""


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
    """Per-session in-memory bookkeeping: just the input queue + a TTL stamp.

    Persistence identity (question_id, recommendation ids) is NOT here — it lives
    in the durable checkpoint state, so it survives eviction of this object.
    """

    pending: Any = None  # next input to stream (AgentState or Command), or None
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
        # One lock per session, handed out under a guard lock. Serialises accept /
        # resume / stream for the same session (see the module docstring).
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

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

    # -- locking ---------------------------------------------------------- #
    def _lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.Lock())

    # -- registry / TTL --------------------------------------------------- #
    def _sweep(self) -> None:
        cutoff = time.monotonic() - SESSION_TTL_SECONDS
        for sid, ctx in list(self._registry.items()):
            if ctx.touched_at >= cutoff:
                continue
            # Never evict a session that is mid-interrupt: a human is being waited
            # on (a clarification reply or a responder outcome).
            if self._next_nodes(sid):
                continue
            del self._registry[sid]

    def _config(self, session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

    def _snapshot(self, session_id: str) -> Any:
        """Durable state snapshot (``.next`` + ``.values``) for this session."""

        session = self._session_factory()
        try:
            return self._graph(session).get_state(self._config(session_id))
        finally:
            session.close()

    def _next_nodes(self, session_id: str) -> tuple[str, ...]:
        """Durable interrupt state: the nodes the graph is paused before (if any)."""

        return tuple(self._snapshot(session_id).next)

    # -- /ask : start a new question -------------------------------------- #
    def start_question(self, session_id: str, asker_id: int, question: str) -> None:
        """Queue a NEW question; reject (409) if the session is busy or paused.

        Validates ``asker_id`` exists (404) before persisting, so a bad id is a
        clean boundary error rather than a mid-flush FK ``IntegrityError``.
        """

        with self._lock(session_id):
            self._sweep()
            ctx = self._registry.get(session_id)
            if ctx is not None and ctx.pending is not None:
                raise SessionConflict("a run is already queued for this session")
            if self._next_nodes(session_id):
                raise SessionConflict("session is awaiting a resume; answer it first")

            now = self._now_factory()
            # Collision-free (uuid4) but ``api_``-prefixed so operational tooling
            # and test cleanup can target API-created questions by prefix.
            question_id = f"api_{uuid.uuid4().hex}"
            with session_scope(self._session_factory) as session:
                if not employee_exists(session, asker_id):
                    raise AskerNotFound(f"asker_id {asker_id} is not a known employee")
                persist_question(session, question_id, asker_id, question, now)
            ctx = self._registry.setdefault(session_id, _SessionCtx())
            ctx.pending = {
                "question": question,
                "asker": {"id": asker_id},
                "now": now,
                "question_id": question_id,
            }
            ctx.touched_at = time.monotonic()

    # -- /answer : resume a paused run ------------------------------------ #
    def submit_resume(
        self, session_id: str, *, outcome: str | None = None, reply: str | None = None
    ) -> None:
        """Queue a resume, validating it matches the pending interrupt kind.

        The outcome target (``primary_recommendation_id``) is read from the durable
        state, not from memory — so it is recorded even after an eviction/restart.
        """

        with self._lock(session_id):
            snapshot = self._snapshot(session_id)
            next_nodes = tuple(snapshot.next)
            if not next_nodes:
                raise SessionConflict("session is not awaiting a resume")
            ctx = self._registry.get(session_id)
            if ctx is not None and ctx.pending is not None:
                # A resume is already queued and not yet streamed (codex#5).
                raise SessionConflict("a resume is already queued for this session")

            node = next_nodes[0]
            if node == "ask":
                if reply is None:
                    raise SessionInvalid("this session expects a clarification 'reply'")
                resume_value: str = reply
            elif node == "send":
                if outcome is None:
                    raise SessionInvalid("this session expects a responder 'outcome'")
                resume_value = outcome
                self._record_outcome(session_id, snapshot.values, outcome)
            else:  # pragma: no cover - the graph only ever interrupts at ask/send
                raise SessionConflict("session cannot be resumed from its current state")

            ctx = self._registry.setdefault(session_id, _SessionCtx())
            ctx.pending = Command(resume=resume_value)
            ctx.touched_at = time.monotonic()

    def _record_outcome(self, session_id: str, values: dict[str, Any], outcome: str) -> None:
        """Record the responder outcome on the durable primary recommendation."""

        primary = values.get("primary_recommendation_id")
        question_id = values.get("question_id")
        with session_scope(self._session_factory) as session:
            if primary is None and question_id is not None:
                # Durable fallback: re-derive the handed-off recommendation from
                # the DB (e.g. a disconnect before update_state persisted the id).
                primary = latest_primary_recommendation(session, question_id)
            if primary is None:
                logger.warning(
                    "no recommendation to record outcome for session %s (outcome=%s)",
                    session_id,
                    outcome,
                )
                return
            set_recommendation_outcome(session, primary, outcome)

    # -- /events : stream ------------------------------------------------- #
    def is_streamable(self, session_id: str) -> bool:
        """True if there is a queued run OR a paused/mid-run state to continue."""

        ctx = self._registry.get(session_id)
        if ctx is not None and ctx.pending is not None:
            return True
        return bool(self._next_nodes(session_id))

    def stream_events(self, session_id: str) -> Iterator[ServerSentEvent]:
        """Stream the queued run, or reconnect to a paused/mid-run state.

        Held under the per-session lock for the whole stream, so a second
        concurrent ``/events`` cannot start the graph a second time (which would
        double-insert recommendations): it blocks, then finds the input already
        consumed and reconnects instead. A fresh DB session is opened for the run
        and always closed; everything is inside try/except so any failure closes
        cleanly with one generic ``error`` event (details logged, never sent).
        """

        with self._lock(session_id):
            session = self._session_factory()
            try:
                yield from self._dispatch_stream(session, session_id)
            except Exception:
                logger.exception("SSE stream failed for session %s", session_id)
                yield _error_event()
            finally:
                session.close()

    def _dispatch_stream(self, session: Session, session_id: str) -> Iterator[ServerSentEvent]:
        graph = self._graph(session)
        config = self._config(session_id)
        ctx = self._registry.get(session_id)
        if ctx is not None and ctx.pending is not None:
            pending = ctx.pending
            ctx.pending = None
            ctx.touched_at = time.monotonic()
            yield from self._run(graph, config, pending)
            return

        state = graph.get_state(config)
        if ctx is not None:
            ctx.touched_at = time.monotonic()
        if not state.next:
            return  # nothing queued and nothing paused: route already 404s
        node = state.next[0]
        if node in _INTERRUPT_NODES:
            reconnect = reconnect_event(node, state.values)
            if reconnect is not None:
                yield reconnect
        else:
            # codex#6: a client disconnected mid-run (before ask/send). The run is
            # parked at a normal node — continue it from the checkpoint.
            yield from self._run(graph, config, None)

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
        self, graph: Any, config: dict[str, Any], agent_input: Any
    ) -> Iterator[ServerSentEvent]:
        """Stream one run segment, persisting topics/recommendations as it goes.

        ``question_id`` / ``now`` come from the fresh input (a ``/ask`` dict) or,
        for a resume / mid-run continuation, from the durable state. After the
        segment, the shown recommendation ids are written back into the state so
        ``/answer`` can record the outcome durably.
        """

        question_id, now = self._run_identity(graph, config, agent_input)
        rec_ids: list[int] | None = None
        for update in graph.stream(agent_input, config, stream_mode="updates"):
            for node, data in update.items():
                if node == "c1_intent":
                    self._persist_topics(question_id, data)
                elif node == "c6_score":
                    rec_ids = self._persist_recommendations(question_id, now, data)
                event = (
                    interrupt_event(_interrupt_payload(data))
                    if node == "__interrupt__"
                    else node_event(node, data)
                )
                if event is not None:
                    yield event
        if rec_ids:
            graph.update_state(
                config,
                {"recommendation_ids": rec_ids, "primary_recommendation_id": rec_ids[0]},
            )

    def _run_identity(
        self, graph: Any, config: dict[str, Any], agent_input: Any
    ) -> tuple[str | None, dt.datetime | None]:
        if isinstance(agent_input, dict):
            return agent_input.get("question_id"), agent_input.get("now")
        values = graph.get_state(config).values
        return values.get("question_id"), values.get("now")

    def _persist_topics(self, question_id: str | None, data: dict[str, Any] | None) -> None:
        if question_id is None:  # pragma: no cover - question_id always set via /ask
            logger.warning("c1 topics with no question_id; skipping persist")
            return
        topics = (data or {}).get("topics") or []
        with session_scope(self._session_factory) as session:
            update_question_topics(session, question_id, topics)

    def _persist_recommendations(
        self, question_id: str | None, now: dt.datetime | None, data: dict[str, Any] | None
    ) -> list[int] | None:
        recs = (data or {}).get("recommendations") or []
        if not recs:
            return None
        if question_id is None or now is None:  # pragma: no cover - both set via /ask
            logger.warning("c6 recommendations with no question_id/now; skipping persist")
            return None
        with session_scope(self._session_factory) as session:
            return insert_shown_recommendations(session, question_id, recs, now)
