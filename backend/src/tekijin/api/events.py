"""LangGraph node updates -> SSE logical events (the mapping lives here only).

``graph.stream(stream_mode="updates")`` yields ``{node_name: partial_state}``
dicts plus a ``{"__interrupt__": (Interrupt,)}`` entry when the run pauses. This
module turns those into ``sse_starlette`` events per model-definition §4:

    c1_intent          -> event: understood
    ask (interrupt)    -> event: followup
    c5_route           -> event: route
    c6_score           -> event: recommend
    c7_draft           -> event: draft
    c8_update          -> event: done
    off_topic/document/unresolved_intent/no_candidate/no_expert -> event: message

All other nodes (reset, c2_sufficiency, c3_embed, c4_retrieve, prior_answer,
send, reroute) are internal and emit nothing.
"""

from __future__ import annotations

from typing import Any

from sse_starlette import ServerSentEvent

from tekijin.api import schemas

# Terminal node -> the ``status`` reported on its ``message`` event.
_TERMINAL_STATUS: dict[str, str] = {
    "off_topic": "off_topic",
    "document": "document",
    "unresolved_intent": "unresolved",
    "no_candidate": "no_candidate",
    # #70: the answerability critic rejected — candidates scored, but their
    # in-house track record was judged insufficient (a graceful terminal, not a
    # hand-off). Only surfaces when the critic is wired (answerability_enabled).
    "no_expert": "no_expert",
    # #291: the assistant answered directly from retrieved data (past Q&A /
    # documents) with citations — no hand-off. Only when self_answer_enabled.
    "self_answered": "self_answered",
}

# Nodes that produce a visible SSE event (everything else is internal). Kept as a
# set for the single-source-of-truth of "which nodes surface to the client".
EVENT_NODES: frozenset[str] = frozenset(
    {"c1_intent", "c5_route", "c6_score", "c7_draft", "c8_update", *_TERMINAL_STATUS}
)

# Nodes whose event ends the run — ``done`` (c8_update) and the terminal messages.
# The service attaches ``latency_ms`` (this segment's processing time) to these.
TERMINAL_NODES: frozenset[str] = frozenset({"c8_update", *_TERMINAL_STATUS})


def _sse(event: str, data: schemas.BaseModel) -> ServerSentEvent:
    return ServerSentEvent(event=event, data=data.model_dump_json())


def node_event(
    node: str, update: dict[str, Any], *, latency_ms: int | None = None
) -> ServerSentEvent | None:
    """Map one node update to an SSE event, or ``None`` for internal nodes.

    ``latency_ms`` (this run segment's processing time) is attached to the terminal
    events (``done`` / ``message``) so the client can surface it; it is ignored for
    non-terminal nodes (#177).
    """

    update = update or {}
    if node == "c1_intent":
        return _sse(
            "understood",
            schemas.UnderstoodData(
                topics=update.get("topics", []),
                products=update.get("products", []),
                situation=update.get("situation"),
                question_type=update.get("question_type"),
                confidence=update.get("intent_confidence", 0.0),
            ),
        )
    if node == "c5_route":
        return _sse(
            "route",
            schemas.RouteData(
                route=update.get("route", "person"),
                reason=update.get("route_reason", ""),
                confidence=update.get("route_confidence", 0.0),
            ),
        )
    if node == "additive_answer":
        # #413: additive cited answer on the person route. Emit a ``reference`` event
        # ONLY when one grounded (text present); otherwise this node is silent and the
        # run proceeds to the plain hand-off. It never replaces the recommend/hand-off.
        text = update.get("additive_answer_text")
        if not text:
            return None
        return _sse(
            "reference",
            schemas.ReferenceData(
                answer=text,
                citations=update.get("additive_citations") or [],
            ),
        )
    if node == "c6_score":
        # person_id crosses the boundary as the external "E###" string form
        # (model-definition §163-170), paired with the "E###" asker_id we accept.
        recs: list[Any] = [
            {**rec, "person_id": schemas.format_employee_id(rec["person_id"])}
            for rec in update.get("recommendations", [])
        ]
        return _sse("recommend", schemas.RecommendData(recommendations=recs))
    if node == "c7_draft":
        return _sse("draft", schemas.DraftData(draft=update.get("draft") or ""))
    if node == "c8_update":
        return _sse(
            "done",
            schemas.DoneData(status="sent", answer=update.get("answer"), latency_ms=latency_ms),
        )
    if node in _TERMINAL_STATUS:
        raw_fallback = update.get("fallback_responder")
        # Build the model here rather than handing MessageData a bare dict: the id
        # still has to be formatted, and constructing it eagerly means a malformed
        # fallback fails at the same place either way — but now with a type the
        # checker can follow (mirrors how service.py builds `responder`).
        fallback = (
            schemas.Recommendation(
                **{
                    **raw_fallback,
                    "person_id": schemas.format_employee_id(raw_fallback["person_id"]),
                }
            )
            if raw_fallback is not None
            else None
        )
        return _sse(
            "message",
            schemas.MessageData(
                status=_TERMINAL_STATUS[node],
                message=update.get("answer") or "",
                # Only the document terminal carries a doc id; harmless None elsewhere.
                doc_id=update.get("document_id"),
                fallback_responder=fallback,
                # #291: the self_answered terminal carries the sources it cited so
                # the chat renders a link per source; empty elsewhere.
                citations=update.get("self_answer_citations") or [],
                latency_ms=latency_ms,
            ),
        )
    return None  # internal node: no event


def reconnect_events(next_node: str, values: dict[str, Any]) -> list[ServerSentEvent]:
    """Re-emit the pending interrupt event(s) when a client reconnects to /events.

    A session paused at ``ask`` re-sends the ``followup`` (from the saved state).
    One paused at ``send`` re-sends BOTH the ``recommend`` (the current
    candidates, from durable state) AND the ``draft`` — so a reconnect fully
    reconstructs the hand-off even after a decline-driven reroute, and so a single
    consumer draining the stream cannot leave a later reconnecting client without
    the candidates (``ResultScreen`` reads candidates only from ``recommend``).
    Any other pending node yields nothing.
    """

    if next_node == "ask":
        return [
            _sse(
                "followup",
                schemas.FollowupData(
                    question=values.get("followup_question") or "",
                    missing=values.get("missing", []),
                ),
            )
        ]
    if next_node == "send":
        out: list[ServerSentEvent] = []
        recs = values.get("recommendations") or []
        if recs:
            # person_id -> external "E###" form, mirroring the live recommend event.
            ext: list[Any] = [
                {**rec, "person_id": schemas.format_employee_id(rec["person_id"])} for rec in recs
            ]
            out.append(_sse("recommend", schemas.RecommendData(recommendations=ext)))
        out.append(_sse("draft", schemas.DraftData(draft=values.get("draft") or "")))
        return out
    return []


# SSE event names that represent a *terminal* run outcome (worth replaying on a
# reconnect after the run has already finished).
TERMINAL_EVENTS: frozenset[str] = frozenset({"done", "message"})


def replay_terminal(values: dict[str, Any]) -> ServerSentEvent | None:
    """Re-emit the stored terminal event (``done`` / ``message``) on reconnect.

    A run that finished commits its final event into ``last_event`` (see the
    service). If a client disconnected before receiving it, reconnecting to
    /events replays it verbatim — read-only, no re-run, no double insert. Returns
    ``None`` when there is no stored terminal event (a genuinely unknown session).
    """

    last = values.get("last_event")
    if not last:
        return None
    return ServerSentEvent(event=last["event"], data=last["data"])


def interrupt_event(payload: dict[str, Any]) -> ServerSentEvent | None:
    """Map an ``interrupt`` payload to an SSE event.

    A ``followup_question`` payload is the C2 clarification -> ``followup``. The
    ``send`` interrupt (draft/responder payload) needs no event: the ``draft``
    was already emitted and the client now POSTs an outcome to /answer.
    """

    if payload and "followup_question" in payload:
        return _sse(
            "followup",
            schemas.FollowupData(
                question=payload.get("followup_question") or "",
                missing=payload.get("missing", []),
            ),
        )
    return None
