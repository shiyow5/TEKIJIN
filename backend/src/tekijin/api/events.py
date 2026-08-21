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
    off_topic/document/unresolved_intent/no_candidate -> event: message

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
}

# Nodes that produce a visible SSE event (everything else is internal). Kept as a
# set for the single-source-of-truth of "which nodes surface to the client".
EVENT_NODES: frozenset[str] = frozenset(
    {"c1_intent", "c5_route", "c6_score", "c7_draft", "c8_update", *_TERMINAL_STATUS}
)


def _sse(event: str, data: schemas.BaseModel) -> ServerSentEvent:
    return ServerSentEvent(event=event, data=data.model_dump_json())


def node_event(node: str, update: dict[str, Any]) -> ServerSentEvent | None:
    """Map one node update to an SSE event, or ``None`` for internal nodes."""

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
    if node == "c6_score":
        return _sse(
            "recommend",
            schemas.RecommendData(recommendations=update.get("recommendations", [])),
        )
    if node == "c7_draft":
        return _sse("draft", schemas.DraftData(draft=update.get("draft") or ""))
    if node == "c8_update":
        return _sse("done", schemas.DoneData(status="sent", answer=update.get("answer")))
    if node in _TERMINAL_STATUS:
        return _sse(
            "message",
            schemas.MessageData(status=_TERMINAL_STATUS[node], message=update.get("answer") or ""),
        )
    return None  # internal node: no event


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
