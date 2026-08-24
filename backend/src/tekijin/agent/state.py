"""Shared graph state for the C1-C8 agent flow.

``AgentState`` is the single ``TypedDict`` threaded through the LangGraph
``StateGraph``. Every node returns a *partial* update (a dict with only the keys
it changed); LangGraph merges those into the running state. Keys are grouped by
the component that writes them (C1 intent, C2 sufficiency, … C8 update) plus a
few control keys (``now``, ``answer``, decline loop).

Nothing here reads the clock or the network — ``now`` is injected by the caller
so the whole run is reproducible.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, TypedDict

# C5 route labels and responder outcomes, as closed sets so a typo is a type
# error and the graph routers can validate against them.
Route = Literal["person", "prior_answer", "document"]
Outcome = Literal["accepted", "declined"]


class PastAnswer(TypedDict):
    """One past-answer hit from C4."""

    qa_id: str
    score: float
    responder_id: int | None  # None if the answer's responder is unknown


class DocumentHit(TypedDict):
    """One document hit from C4."""

    doc_id: str
    score: float


class RetrievalResult(TypedDict):
    """The C4 (HybridRetriever) output shape shared by C5/C6.

    The ``*_score`` values inside ``past_answers`` / ``documents`` are RRF fusion
    scores — good for *ranking* within a channel, but they carry no absolute
    meaning across queries. So each channel also reports its top absolute dense
    cosine similarity (``*_confidence``, in ``[0, 1]``, ``0.0`` when the channel is
    empty); C5 routes on those, not on the RRF scores.
    """

    past_answers: list[PastAnswer]
    documents: list[DocumentHit]
    candidate_people: list[int]
    answer_confidence: float
    document_confidence: float
    people_confidence: float


def empty_retrieval() -> RetrievalResult:
    """A fresh, empty :class:`RetrievalResult` (the default before C4 runs)."""

    return {
        "past_answers": [],
        "documents": [],
        "candidate_people": [],
        "answer_confidence": 0.0,
        "document_confidence": 0.0,
        "people_confidence": 0.0,
    }


class AgentState(TypedDict, total=False):
    """The full state of one question's journey through the graph."""

    # -- input -------------------------------------------------------------
    question: str
    asker: dict[str, Any] | None
    now: dt.datetime  # naive; the scorer requires it

    # -- durable persistence identity (set by the API layer) ---------------
    # These live in the checkpoint so that outcome recording survives a
    # process restart / eviction and does not depend on the volatile
    # in-memory session registry.
    question_id: str | None  # uuid4 assigned at POST /ask
    recommendation_ids: list[int]  # DB ids of every *shown* recommendation
    primary_recommendation_id: int | None  # the one actually handed off
    # The last terminal SSE event ({"event", "data"}) of a completed run, kept so
    # a client that disconnected before receiving ``done`` / terminal ``message``
    # can re-fetch it on reconnect (EventSource retry) — read-only replay.
    last_event: dict[str, Any] | None

    # -- C1 intent ---------------------------------------------------------
    topics: list[str]
    products: list[str]
    situation: str | None
    question_type: str
    out_of_scope: bool
    intent_confidence: float
    # Topics the QUESTION itself yielded (topics minus retrieval-mediated ones,
    # #69/#276): the graph's identifiability signal for the unresolved terminal.
    topics_from_question: list[str]

    # -- C2 sufficiency ----------------------------------------------------
    sufficient: bool
    missing: list[str]
    followup_question: str | None
    followup_count: int  # how many times we have asked back (cap = 1)
    intent_unresolved: bool  # capped but still no topic -> graceful terminal

    # -- C3 embedding ------------------------------------------------------
    query_vector: list[float]

    # -- C4 retrieval ------------------------------------------------------
    retrieval: RetrievalResult

    # -- C5 route ----------------------------------------------------------
    route: Route
    route_reason: str
    route_confidence: float
    prior_answer_note: str | None  # set on the prior_answer (補助) route
    pinned_responder_id: int | None  # prior_answer: hand off to THIS person

    # -- C6 scoring --------------------------------------------------------
    recommendations: list[dict[str, Any]]

    # -- C7 draft ----------------------------------------------------------
    draft: str | None

    # -- decline loop / C8 -------------------------------------------------
    outcome: Outcome | None  # from the responder (validated at the send router)
    declined_ids: list[int]

    # -- terminal output ---------------------------------------------------
    answer: str | None
    document_id: str | None  # document route: the cited doc, surfaced to the client
