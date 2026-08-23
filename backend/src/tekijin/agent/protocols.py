"""LLM-node interfaces (C1 / C2 / C7) and their structured results.

The three LLM components sit behind these ``Protocol``s so the deterministic
stubs used today can be swapped for real vLLM-backed implementations later
without touching the graph. A future implementation would build each model with
``langchain.chat_models.init_chat_model("openai:<model>",
base_url=settings.llm_base_url, api_key=settings.llm_api_key)`` and
``.with_structured_output(...)`` for C1/C2 — see ``stubs.py`` for the contract
each must satisfy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from tekijin.agent.state import RetrievalResult


@dataclass(frozen=True, slots=True)
class IntentResult:
    """C1 output: the question parsed into a searchable structure."""

    topics: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    situation: str | None = None
    question_type: str = "製品QA"
    out_of_scope: bool = False
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class SufficiencyResult:
    """C2 output: whether enough is known, and one combined follow-up if not."""

    sufficient: bool = True
    missing: list[str] = field(default_factory=list)
    followup_question: str | None = None


class IntentModel(Protocol):
    """C1: free-text question -> :class:`IntentResult` (structured)."""

    def analyze(self, question: str, asker: dict[str, Any] | None) -> IntentResult: ...


class SufficiencyModel(Protocol):
    """C2: decide if the question is answerable or needs one clarification."""

    def check(
        self, question: str, intent: IntentResult, followup_count: int
    ) -> SufficiencyResult: ...


class DraftModel(Protocol):
    """C7: compose a polite hand-off request to the chosen responder."""

    def draft(
        self,
        question: str,
        responder: dict[str, Any],
        asker: dict[str, Any] | None,
        missing: list[str],
        *,
        situation: str | None = None,
        topics: list[str] | None = None,
        known_values: dict[str, str] | None = None,
    ) -> str: ...


class Retriever(Protocol):
    """C4: hybrid search returning the :class:`RetrievalResult` shape.

    ``query_vector`` is the optional C3 embedding of ``query`` (reused by the
    dense channels to avoid a second embedding call).
    """

    def search(
        self, query: str, *, query_vector: Sequence[float] | None = None
    ) -> RetrievalResult: ...
