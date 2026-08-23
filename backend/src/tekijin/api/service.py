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
in-process); that is documented on the Makefile ``serve-prod`` target.
"""

from __future__ import annotations

import contextlib
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
from tekijin.api.events import (
    TERMINAL_EVENTS,
    interrupt_event,
    node_event,
    reconnect_events,
    replay_terminal,
)
from tekijin.data.db import session_scope
from tekijin.data.handoff import employee_brief, responder_reuse_stats
from tekijin.data.writes import (
    employee_exists,
    insert_shown_recommendations,
    latest_primary_recommendation,
    mark_question_resolved,
    persist_question,
    recommendation_outcome,
    set_recommendation_outcome,
    update_question_route,
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


class HandoffNotFound(Exception):
    """No responder handoff is available for this session (maps to HTTP 404)."""


class ServiceBusy(Exception):
    """Too many runs are executing on the (single-GPU) LLM (maps to HTTP 503).

    Backpressure (#180): the app sheds NEW questions when at capacity rather than
    piling more concurrent graph runs onto vLLM and risking OOM / a host stall. The
    hard per-GPU bound is still vLLM's own ``max-num-seqs``; this is the app-level
    admission gate so callers get a fast, graceful "混雑中" instead of a stuck run.
    """


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
        bm25_weight: float | None = None,
        max_concurrent_runs: int = 0,
        now_factory: Any = _default_now,
        clock: Any = time.monotonic,
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
        # C4 BM25 fusion weight from the SUPPLIED settings (via the factory), so a
        # custom Settings is honored rather than the cached global (#68). None →
        # the default HybridRetriever reads settings itself.
        self._bm25_weight = bm25_weight
        # Backpressure (#180): max graph runs executing at once before /ask sheds new
        # questions with 503. 0 (default) disables it — set from settings via the
        # factory in production. Guarded by its own lock; independent of the per-
        # session locks so admission is a cheap global check.
        self._max_concurrent_runs = max_concurrent_runs
        self._active_runs = 0
        self._runs_lock = threading.Lock()
        self._now_factory = now_factory
        # Monotonic clock for TTL bookkeeping — injectable so ``_sweep`` is
        # deterministic in tests (the process ``time.monotonic`` epoch is the boot
        # time, which is arbitrary and, on a fresh CI runner, small; see #0).
        self._clock = clock
        self._registry: dict[str, _SessionCtx] = {}
        self._registry_guard = threading.Lock()  # guards _registry membership only
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

    # -- backpressure (#180) ---------------------------------------------- #
    def _reject_if_saturated(self) -> None:
        """Raise :class:`ServiceBusy` when the LLM run pool is full (admission gate)."""

        if self._max_concurrent_runs <= 0:
            return
        with self._runs_lock:
            if self._active_runs >= self._max_concurrent_runs:
                raise ServiceBusy(
                    f"{self._active_runs} runs already executing "
                    f"(max {self._max_concurrent_runs}); try again shortly"
                )

    @contextlib.contextmanager
    def _run_slot(self) -> Iterator[None]:
        """Count one in-flight graph run for the duration of its execution.

        Held only while ``graph.stream`` actually runs (LLM/GPU work), so a run that
        pauses at an interrupt or finishes releases its slot; the closing generator's
        ``finally`` runs on normal completion AND on client disconnect, so slots are
        not leaked. Only NEW questions are shed (see ``start_question``); resumes and
        mid-run continuations still occupy a slot but are never rejected.
        """

        with self._runs_lock:
            self._active_runs += 1
        try:
            yield
        finally:
            with self._runs_lock:
                self._active_runs -= 1

    # -- locking ---------------------------------------------------------- #
    def _lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.Lock())

    # -- registry (membership changes go through these, under the guard) --- #
    def _reg_get(self, session_id: str) -> _SessionCtx | None:
        with self._registry_guard:
            return self._registry.get(session_id)

    def _reg_ensure(self, session_id: str) -> _SessionCtx:
        with self._registry_guard:
            return self._registry.setdefault(session_id, _SessionCtx())

    # -- TTL sweep -------------------------------------------------------- #
    def _sweep(self) -> None:
        """Evict idle sessions (and their locks); never a mid-interrupt one.

        Runs opportunistically on ``/ask``. Membership changes are made under the
        registry guard; a candidate is only evicted while its per-session lock is
        held (non-blocking), so it cannot race a live dispatch on that session.
        """

        cutoff = self._clock() - SESSION_TTL_SECONDS
        with self._registry_guard:
            candidates = [sid for sid, ctx in self._registry.items() if ctx.touched_at < cutoff]
        for sid in candidates:
            # A human is being waited on (clarification / outcome): keep it.
            if self._next_nodes(sid):
                continue
            lock = self._lock(sid)
            if not lock.acquire(blocking=False):
                continue  # a dispatch is touching this session now — next sweep
            try:
                with self._registry_guard:
                    ctx = self._registry.get(sid)
                    if ctx is None or ctx.touched_at >= cutoff:
                        continue  # revived since the scan above
                    del self._registry[sid]
                with self._locks_guard:  # GC the lock so ids don't leak (codex#2)
                    self._locks.pop(sid, None)
            finally:
                lock.release()

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
            # Backpressure: shed a NEW question when the LLM run pool is saturated,
            # before persisting anything, so the caller gets a fast 503 (#180).
            self._reject_if_saturated()
            ctx = self._reg_get(session_id)
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
                persist_question(
                    session, question_id, asker_id, question, now, session_id=session_id
                )
            ctx = self._reg_ensure(session_id)
            ctx.pending = {
                "question": question,
                "asker": {"id": asker_id},
                "now": now,
                "question_id": question_id,
            }
            ctx.touched_at = self._clock()

    # -- /answer : resume a paused run ------------------------------------ #
    def submit_resume(
        self,
        session_id: str,
        *,
        outcome: str | None = None,
        reply: str | None = None,
        recommendation_id: int | None = None,
    ) -> None:
        """Queue a resume, validating it matches the pending interrupt kind.

        The outcome target (``primary_recommendation_id``) is read from the durable
        state, not from memory — so it is recorded even after an eviction/restart.

        ``recommendation_id`` is an optional generation token: when supplied with an
        ``outcome`` it must equal the session's current primary, else the outcome is
        stale (a reroute moved the hand-off on, or a competing tab) and is rejected
        with :class:`SessionConflict` (409) — so it never binds to a new candidate
        (#94). ``None`` skips the check for older clients / clarification replies.
        """

        with self._lock(session_id):
            snapshot = self._snapshot(session_id)
            next_nodes = tuple(snapshot.next)
            if not next_nodes:
                raise SessionConflict("session is not awaiting a resume")
            ctx = self._reg_get(session_id)
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
                # Stale-outcome guard (#94): if the client echoed the recommendation
                # id it was shown and the session has since moved to a different
                # primary (reroute / competing tab), reject so the outcome cannot
                # bind to the new candidate. A missing/None current primary can't be
                # validated, so we let it through (degrade to prior behavior).
                current_primary = snapshot.values.get("primary_recommendation_id")
                if (
                    recommendation_id is not None
                    and current_primary is not None
                    and recommendation_id != current_primary
                ):
                    raise SessionConflict(
                        "this hand-off has moved to a different candidate; reload and try again"
                    )
                # The DB is the source of truth for the outcome. ``_record_outcome``
                # writes it first-wins and returns the EFFECTIVE (persisted) value:
                # on a duplicate submission (e.g. a restart lost the in-memory
                # pending guard and the responder resubmitted) the stored outcome
                # wins. We resume the graph with that effective value so the
                # checkpoint always advances consistently with the DB — never
                # diverging, and never left permanently paused at ``send``.
                _status, resume_value = self._record_outcome(session_id, snapshot.values, outcome)
            else:  # pragma: no cover - the graph only ever interrupts at ask/send
                raise SessionConflict("session cannot be resumed from its current state")

            ctx = self._reg_ensure(session_id)
            ctx.pending = Command(resume=resume_value)
            ctx.touched_at = self._clock()

    def _record_outcome(
        self, session_id: str, values: dict[str, Any], outcome: str
    ) -> tuple[str, str]:
        """Record the responder outcome on the durable primary recommendation.

        Returns ``(status, effective_outcome)`` where ``effective_outcome`` is the
        value the graph should resume with (the DB is authoritative):

        * ``("recorded", outcome)`` — fresh write of the submitted outcome;
        * ``("already", stored)`` — the primary already carried ``stored`` (a
          duplicate submission); the stored value wins, and the caller resumes the
          graph with it so the checkpoint stays consistent instead of diverging;
        * ``("no_target", outcome)`` — no recommendation to attach it to; resume
          with the submitted value (nothing to reconcile against).
        """

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
                return "no_target", outcome
            existing = recommendation_outcome(session, primary)
            if existing is not None:
                return "already", existing
            set_recommendation_outcome(session, primary, outcome)
            # An accepted hand-off resolves the question — stamp the runtime
            # resolution time (first-wins) for the dashboard's avg-resolution (#97).
            if outcome == "accepted" and question_id is not None:
                mark_question_resolved(session, question_id, self._now_factory())
            return "recorded", outcome

    # -- /handoff : responder-facing view (product-spec 画面4) ------------- #
    def get_handoff(self, session_id: str) -> schemas.HandoffResponse:
        """Assemble the responder-facing payload for a ``send``-interrupt session.

        Read-only: reads the durable checkpoint (never advances the graph) plus
        two DB lookups (asker identity, responder reuse totals). Raises
        :class:`HandoffNotFound` (404) when there is no paused run at all
        (unknown / finished); :class:`SessionConflict` (409) when the run is
        paused elsewhere (a clarification is owed to the asker, not a responder
        outcome).

        The snapshot + pending check runs under the per-session lock (as
        ``submit_resume`` / ``stream_events`` do) so it is atomic against a
        concurrent dispatch: a reload cannot pass the "still awaiting" check on a
        stale snapshot while another reader is mid-way through advancing the graph.
        """

        with self._lock(session_id):
            snapshot = self._snapshot(session_id)
            next_nodes = tuple(snapshot.next)
            if not next_nodes:
                raise HandoffNotFound("no responder handoff for this session")
            if next_nodes[0] != "send":
                raise SessionConflict("session is not awaiting a responder outcome")
            # An outcome already queued (submitted, not yet consumed by an /events
            # reader) leaves the durable snapshot at ``send`` with the same, already
            # decided recommendation. Treat it as no-longer-offerable so a reload
            # does not re-render the form and invite a second submission (409).
            ctx = self._reg_get(session_id)
            if ctx is not None and ctx.pending is not None:
                raise HandoffNotFound("this handoff has already been answered")

        values = snapshot.values
        recs = values.get("recommendations") or []
        primary = recs[0] if recs else None
        asker_id = (values.get("asker") or {}).get("id")

        responder: schemas.Recommendation | None = None
        reuse = {"reuse_count": 0, "helpful_answer_count": 0}
        asker_name: str | None = None
        asker_dept: str | None = None
        with session_scope(self._session_factory) as session:
            if asker_id is not None:
                asker_name, asker_dept = employee_brief(session, asker_id)
            if primary is not None:
                # person_id crosses the boundary in the external "E###" form,
                # mirroring the recommend event (events.py / model-definition).
                responder = schemas.Recommendation(
                    **{**primary, "person_id": schemas.format_employee_id(primary["person_id"])}
                )
                reuse = responder_reuse_stats(session, primary["person_id"])

        return schemas.HandoffResponse(
            session_id=session_id,
            question=values.get("question") or "",
            asker=schemas.HandoffAsker(
                id=schemas.format_employee_id(asker_id) if asker_id is not None else "",
                name=asker_name,
                dept=asker_dept,
            ),
            topics=values.get("topics") or [],
            products=values.get("products") or [],
            situation=values.get("situation"),
            missing=values.get("missing") or [],
            responder=responder,
            draft=values.get("draft") or "",
            reuse_count=reuse["reuse_count"],
            helpful_answer_count=reuse["helpful_answer_count"],
            recommendation_id=values.get("primary_recommendation_id"),
        )

    # -- /events : stream ------------------------------------------------- #
    def is_streamable(self, session_id: str) -> bool:
        """True if there is a queued run, a paused run, or a replayable terminal.

        The terminal case lets a client that dropped before receiving ``done`` /
        the terminal ``message`` reconnect and re-fetch it (codex#3). A genuinely
        unknown session has no checkpoint at all → not streamable → 404.
        """

        ctx = self._reg_get(session_id)
        if ctx is not None and ctx.pending is not None:
            return True
        snapshot = self._snapshot(session_id)
        return bool(snapshot.next) or snapshot.values.get("last_event") is not None

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
        ctx = self._reg_get(session_id)
        if ctx is not None and ctx.pending is not None:
            pending = ctx.pending
            ctx.pending = None
            ctx.touched_at = self._clock()
            yield from self._run(graph, config, pending)
            return

        state = graph.get_state(config)
        if ctx is not None:
            ctx.touched_at = self._clock()
        if not state.next:
            # Finished run: replay its terminal event so a reconnecting client can
            # still receive done / message (codex#3). Unknown session → nothing.
            replay = replay_terminal(state.values)
            if replay is not None:
                yield replay
            return
        node = state.next[0]
        if node in _INTERRUPT_NODES:
            yield from reconnect_events(node, state.values)
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
            bm25_weight=self._bm25_weight,
        )

    def _run(
        self, graph: Any, config: dict[str, Any], agent_input: Any
    ) -> Iterator[ServerSentEvent]:
        """Stream one run segment, persisting topics/recommendations as it goes.

        ``question_id`` comes from the fresh input (a ``/ask`` dict) or, for a
        resume / mid-run continuation, from the durable state. After the segment,
        the shown recommendation ids AND the terminal event (if the run finished)
        are written back into the state — the former so ``/answer`` records the
        outcome durably, the latter so a reconnect can replay ``done`` / ``message``.
        """

        question_id = self._run_question_id(graph, config, agent_input)
        rec_ids: list[int] | None = None
        terminal: ServerSentEvent | None = None
        # Occupy a backpressure slot only while the graph actually executes (#180);
        # released by the finally on completion or client disconnect.
        with self._run_slot():
            for update in graph.stream(agent_input, config, stream_mode="updates"):
                for node, data in update.items():
                    if node == "c1_intent":
                        self._persist_topics(question_id, data)
                    elif node == "c5_route":
                        self._persist_route(question_id, data)
                    elif node == "c6_score":
                        rec_ids = self._persist_recommendations(question_id, data)
                    event = (
                        interrupt_event(_interrupt_payload(data))
                        if node == "__interrupt__"
                        else node_event(node, data)
                    )
                    if event is not None:
                        if event.event in TERMINAL_EVENTS:
                            terminal = event
                        yield event
            self._persist_run_state(graph, config, rec_ids, terminal)

    def _persist_run_state(
        self,
        graph: Any,
        config: dict[str, Any],
        rec_ids: list[int] | None,
        terminal: ServerSentEvent | None,
    ) -> None:
        updates: dict[str, Any] = {}
        if rec_ids:
            updates["recommendation_ids"] = rec_ids
            updates["primary_recommendation_id"] = rec_ids[0]
        if terminal is not None:
            updates["last_event"] = {"event": terminal.event, "data": terminal.data}
        if updates:
            graph.update_state(config, updates)

    def _run_question_id(self, graph: Any, config: dict[str, Any], agent_input: Any) -> str | None:
        if isinstance(agent_input, dict):
            return agent_input.get("question_id")
        return graph.get_state(config).values.get("question_id")

    def _persist_route(self, question_id: str | None, data: dict[str, Any] | None) -> None:
        if question_id is None:  # pragma: no cover - question_id always set via /ask
            logger.warning("c5 route with no question_id; skipping persist")
            return
        route = (data or {}).get("route")
        if route is None:  # pragma: no cover - c5 always sets a route
            return
        with session_scope(self._session_factory) as session:
            update_question_route(session, question_id, route)
            # A self-resolving route (document) ends the run with no human hand-off,
            # so the question is resolved the moment it is routed there (#97).
            if route == "document":
                mark_question_resolved(session, question_id, self._now_factory())

    def _persist_topics(self, question_id: str | None, data: dict[str, Any] | None) -> None:
        if question_id is None:  # pragma: no cover - question_id always set via /ask
            logger.warning("c1 topics with no question_id; skipping persist")
            return
        topics = (data or {}).get("topics") or []
        with session_scope(self._session_factory) as session:
            update_question_topics(session, question_id, topics)

    def _persist_recommendations(
        self, question_id: str | None, data: dict[str, Any] | None
    ) -> list[int] | None:
        recs = (data or {}).get("recommendations") or []
        if not recs:
            return None
        if question_id is None:  # pragma: no cover - question_id always set via /ask
            logger.warning("c6 recommendations with no question_id; skipping persist")
            return None
        with session_scope(self._session_factory) as session:
            return insert_shown_recommendations(session, question_id, recs)
