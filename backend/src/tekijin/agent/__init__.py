"""Agent orchestration (Issue #31): the C1-C8 flow as a LangGraph StateGraph.

Public surface for the API layer (#32):

* :func:`~tekijin.agent.graph.build_agent` — compile the flow (injectable LLM
  stubs, retriever, scorer, checkpointer).
* :class:`~tekijin.agent.state.AgentState` — the shared graph state.
* :func:`~tekijin.agent.route.decide_route` — the deterministic C5 decision.
* :mod:`~tekijin.agent.protocols` / :mod:`~tekijin.agent.stubs` — the LLM-node
  interfaces and their deterministic defaults.
"""

from __future__ import annotations

from tekijin.agent.graph import build_agent
from tekijin.agent.protocols import (
    DraftModel,
    IntentModel,
    IntentResult,
    SufficiencyModel,
    SufficiencyResult,
)
from tekijin.agent.route import RouteDecision, decide_route
from tekijin.agent.state import AgentState
from tekijin.agent.stubs import (
    KeywordIntentModel,
    RuleSufficiencyModel,
    TemplateDraftModel,
)

__all__ = [
    "AgentState",
    "DraftModel",
    "IntentModel",
    "IntentResult",
    "KeywordIntentModel",
    "RouteDecision",
    "RuleSufficiencyModel",
    "SufficiencyModel",
    "SufficiencyResult",
    "TemplateDraftModel",
    "build_agent",
    "decide_route",
]
