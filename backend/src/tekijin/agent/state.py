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
from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """The full state of one question's journey through the graph."""

    # -- input -------------------------------------------------------------
    question: str
    asker: dict[str, Any] | None
    now: dt.datetime  # naive; the scorer requires it

    # -- C1 intent ---------------------------------------------------------
    topics: list[str]
    products: list[str]
    situation: str | None
    question_type: str
    out_of_scope: bool
    intent_confidence: float

    # -- C2 sufficiency ----------------------------------------------------
    sufficient: bool
    missing: list[str]
    followup_question: str | None
    followup_count: int  # how many times we have asked back (cap = 1)

    # -- C3 embedding ------------------------------------------------------
    query_vector: list[float]

    # -- C4 retrieval ------------------------------------------------------
    retrieval: dict[str, Any]

    # -- C5 route ----------------------------------------------------------
    route: str  # person | prior_answer | document
    route_reason: str
    route_confidence: float
    prior_answer_note: str | None  # set on the prior_answer (補助) route

    # -- C6 scoring --------------------------------------------------------
    recommendations: list[dict[str, Any]]

    # -- C7 draft ----------------------------------------------------------
    draft: str | None

    # -- decline loop / C8 -------------------------------------------------
    outcome: str | None  # accepted | declined (from the responder)
    declined_ids: list[int]

    # -- terminal output ---------------------------------------------------
    answer: str | None
