"""Assemble the C1-C8 flow as a LangGraph ``StateGraph``.

The wiring mirrors the model-definition §1 flowchart one-to-one: rounded boxes
become ``add_node`` and diamonds become ``add_conditional_edges``. LLM nodes
(C1/C2/C7) default to the deterministic stubs; the retriever (C4) and scorer (C6)
default to the real modules but are injectable, so tests can force any branch
without a model, a network call, or retrieval nondeterminism.

Persistence uses an injected checkpointer (default ``MemorySaver``); #32 swaps in
``PostgresSaver`` without touching this graph. ``thread_id`` (per session) drives
``interrupt``/``resume``.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from tekijin.agent.nodes import AgentNodes
from tekijin.agent.protocols import DraftModel, IntentModel, Retriever, SufficiencyModel
from tekijin.agent.route import DOCUMENT, PERSON, PRIOR_ANSWER
from tekijin.agent.state import AgentState
from tekijin.agent.stubs import KeywordIntentModel, RuleSufficiencyModel, TemplateDraftModel
from tekijin.data.repository import Repository
from tekijin.retrieval.embedding import Embedder
from tekijin.retrieval.retriever import HybridRetriever
from tekijin.scorer.scorer import ExpertiseScorer
from tekijin.scorer.weights import DEFAULT_WEIGHTS, Weights


# -- conditional-edge routers (pure) -------------------------------------
def _after_c1(state: AgentState) -> str:
    # Node id differs from the "out_of_scope" state key (LangGraph forbids reuse).
    return "off_topic" if state.get("out_of_scope") else "c2_sufficiency"


def _after_c2(state: AgentState) -> str:
    # Capped but still no topic -> graceful "couldn't identify the request"
    # terminal (never silently search on an empty intent and hit no_candidate).
    if state.get("intent_unresolved"):
        return "unresolved_intent"
    # #69: retrieval already ran BEFORE C1 (retrieve-then-classify), so a
    # sufficient intent proceeds straight to routing; only a clarification loops
    # back through ``ask`` (which re-embeds + re-retrieves the enriched question).
    return "c5_route" if state.get("sufficient") else "ask"


def _after_c5(state: AgentState) -> str:
    return state.get("route", PERSON)


def _after_c6(state: AgentState) -> str:
    return "c7_draft" if state.get("recommendations") else "no_candidate"


def _after_send(state: AgentState) -> str:
    # The outcome is external input (Command(resume=...)); validate it. Only the
    # known values proceed — anything else (None, a typo, a payload) loops back to
    # ``send`` to re-confirm, so a bad value never silently reaches the success
    # terminal (c8_update).
    outcome = state.get("outcome")
    if outcome == "declined":
        return "reroute"
    if outcome == "accepted":
        return "c8_update"
    if outcome == "redraft":
        # Asker asked to regenerate the hand-off text ("下書きの作り直し", #260): go
        # back to C7 for the SAME top candidate, then ``c7_draft`` pauses at ``send``
        # again. The regeneration rides the SSE thought-process like any C7 run.
        return "c7_draft"
    return "send"


def build_agent(
    embedder: Embedder,
    session: Any,
    *,
    intent_model: IntentModel | None = None,
    sufficiency_model: SufficiencyModel | None = None,
    draft_model: DraftModel | None = None,
    retriever: Retriever | None = None,
    scorer: ExpertiseScorer | None = None,
    weights: Weights = DEFAULT_WEIGHTS,
    checkpointer: Any | None = None,
    retriever_top_k: int = 10,
    rrf_k: int = 60,
    bm25_weight: float | None = None,
):
    """Compile and return the C1-C8 agent graph.

    Args:
        embedder: Query embedder (C3); FakeEmbedder in tests.
        session: SQLAlchemy session for the default retriever/scorer.
        intent_model / sufficiency_model / draft_model: LLM-node stubs; default to
            the deterministic keyword/rule/template implementations.
        retriever: C4 component with ``.search(query) -> dict``; default
            ``HybridRetriever``.
        scorer: C6 scorer; default ``ExpertiseScorer`` over ``session``.
        checkpointer: LangGraph checkpointer; default ``MemorySaver``.
    """

    nodes = AgentNodes(
        intent_model=intent_model or KeywordIntentModel(),
        sufficiency_model=sufficiency_model or RuleSufficiencyModel(),
        draft_model=draft_model or TemplateDraftModel(),
        embedder=embedder,
        retriever=retriever
        or HybridRetriever(
            embedder, session, top_k=retriever_top_k, rrf_k=rrf_k, bm25_weight=bm25_weight
        ),
        scorer=scorer or ExpertiseScorer(Repository(session), weights=weights),
        # #69: C1 mediates topic classification with the retrieved fragments' text,
        # re-hydrated by id from this repository (see nodes._intent_context).
        fragment_source=Repository(session),
    )

    graph = StateGraph(AgentState)
    graph.add_node("reset", nodes.reset)
    graph.add_node("c1_intent", nodes.c1_intent)
    graph.add_node("c2_sufficiency", nodes.c2_sufficiency)
    graph.add_node("ask", nodes.ask)
    graph.add_node("c3_embed", nodes.c3_embed)
    graph.add_node("c4_retrieve", nodes.c4_retrieve)
    graph.add_node("c5_route", nodes.c5_route)
    graph.add_node("prior_answer", nodes.prior_answer)
    graph.add_node("c6_score", nodes.c6_score)
    graph.add_node("c7_draft", nodes.c7_draft)
    graph.add_node("send", nodes.send)
    graph.add_node("reroute", nodes.reroute)
    graph.add_node("c8_update", nodes.c8_update)
    graph.add_node("off_topic", nodes.out_of_scope)
    graph.add_node("document", nodes.document)
    graph.add_node("no_candidate", nodes.no_candidate)
    graph.add_node("unresolved_intent", nodes.unresolved_intent)

    # START -> reset -> C3 embed -> C4 retrieve -> C1. ``reset`` clears per-question
    # control fields on a fresh invoke; ``resume`` bypasses START, so mid-flow
    # interrupts keep their state. #69: retrieval runs BEFORE C1 so C1 classifies
    # the topic with the retrieved evidence's vocabulary in front of it.
    graph.add_edge(START, "reset")
    graph.add_edge("reset", "c3_embed")
    graph.add_edge("c3_embed", "c4_retrieve")
    graph.add_edge("c4_retrieve", "c1_intent")
    graph.add_conditional_edges(
        "c1_intent",
        _after_c1,
        {"off_topic": "off_topic", "c2_sufficiency": "c2_sufficiency"},
    )
    graph.add_conditional_edges(
        "c2_sufficiency",
        _after_c2,
        {"c5_route": "c5_route", "ask": "ask", "unresolved_intent": "unresolved_intent"},
    )
    # A clarification enriches the question, so re-embed + re-retrieve before
    # re-classifying (the fragments must reflect the updated question).
    graph.add_edge("ask", "c3_embed")
    graph.add_conditional_edges(
        "c5_route",
        _after_c5,
        {PERSON: "c6_score", PRIOR_ANSWER: "prior_answer", DOCUMENT: "document"},
    )
    graph.add_edge("prior_answer", "c6_score")
    graph.add_conditional_edges(
        "c6_score", _after_c6, {"c7_draft": "c7_draft", "no_candidate": "no_candidate"}
    )
    graph.add_edge("c7_draft", "send")
    graph.add_conditional_edges(
        "send",
        _after_send,
        {
            "reroute": "reroute",
            "c8_update": "c8_update",
            "c7_draft": "c7_draft",  # redraft loop (#260): regenerate then re-pause at send
            "send": "send",
        },
    )
    graph.add_edge("reroute", "c6_score")
    graph.add_edge("c8_update", END)
    graph.add_edge("off_topic", END)
    graph.add_edge("document", END)
    graph.add_edge("no_candidate", END)
    graph.add_edge("unresolved_intent", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
