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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from tekijin.agent.state import RetrievalResult

if TYPE_CHECKING:  # pragma: no cover - typing only (keeps the heavy retrieval import out)
    from tekijin.retrieval.fragments import CitedEvidence


@dataclass(frozen=True, slots=True)
class IntentResult:
    """C1 output: the question parsed into a searchable structure."""

    topics: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    situation: str | None = None
    question_type: str = "製品QA"
    out_of_scope: bool = False
    confidence: float = 0.0
    # #83: a location the asker explicitly asked the RESPONDER to be at. ``None``
    # (the overwhelming majority) means no constraint. Always one of
    # ``BRANCH_VOCABULARY`` when set — C1's adapter drops anything else.
    constraint_branch: str | None = None


@dataclass(frozen=True, slots=True)
class SufficiencyResult:
    """C2 output: whether enough is known, and one combined follow-up if not."""

    sufficient: bool = True
    missing: list[str] = field(default_factory=list)
    followup_question: str | None = None


@dataclass(frozen=True, slots=True)
class AnswerabilityResult:
    """Evidence-sufficiency verdict (#70): can the company answer this in-house?

    ``confidence`` is a 0–100 score of whether the shown candidates' track record
    is enough to answer the question — a SEPARATE judgement from topic
    classification (CRAG/Self-RAG critique). A low score means "no adequate expert
    in-house": the graph should reject to a graceful terminal instead of handing
    off to a weak match. Measured (#65/#67 §6): asked as a NUMBER it rejects 4/5
    of the untraceable cases at a 30–70 threshold (misreject 3/45); asked as a
    boolean it over-rejects (18/45), so this must stay numeric.
    """

    confidence: int = 0
    reason: str | None = None


class IntentModel(Protocol):
    """C1: free-text question -> :class:`IntentResult` (structured).

    ``context`` (optional, #69) carries short text snippets of the retrieved
    evidence (past Q&A / documents) so C1 can classify the topic with that
    vocabulary in front of it — the retrieve-then-classify order. It is reference
    data, never the user's ask; ``None`` reproduces the pre-#69 behaviour.
    """

    def analyze(
        self,
        question: str,
        asker: dict[str, Any] | None,
        *,
        context: Sequence[str] | None = None,
    ) -> IntentResult: ...


class SufficiencyModel(Protocol):
    """C2: decide if the question is answerable or needs one clarification."""

    def check(
        self, question: str, intent: IntentResult, followup_count: int
    ) -> SufficiencyResult: ...


class AnswerabilityModel(Protocol):
    """Evidence-sufficiency critic (#70): rate 0–100 whether the company can answer.

    Given the question and the shown candidates' track-record summaries, returns
    an :class:`AnswerabilityResult`. This runs as a SEPARATE step from topic
    classification (it can run in parallel with C1) — a plausible topic does not
    imply an in-house expert, which is exactly why the untraceable cases (#70) are
    unreachable by the classifier alone. ``candidate_evidence`` is a short summary
    line per top candidate (empty when C6 found nobody).
    """

    def assess(self, question: str, candidate_evidence: Sequence[str]) -> AnswerabilityResult: ...


@dataclass(frozen=True, slots=True)
class SelfAnswerResult:
    """Self-answer output (#291): a grounded answer composed from retrieved data.

    The product pivot (#291) drops "the answer is always a person":
    when the past Q&A / internal documents already hold the answer, the assistant
    replies DIRECTLY, citing its sources, and does not hand off. ``cited_source_ids``
    are the ids of the evidence actually used (a subset of what was supplied) so the
    chat can render links back to each source. ``grounded`` is ``False`` when the
    evidence is insufficient to answer — the graph then falls back to routing to a
    person (the tacit-knowledge path), never emits an ungrounded answer. A
    ``grounded`` result ALWAYS carries at least one real citation: a grounded answer
    with no surviving citation is treated as fabricated and downgraded to routing.
    """

    answer: str = ""
    cited_source_ids: list[str] = field(default_factory=list)
    grounded: bool = False


class SelfAnswerModel(Protocol):
    """Self-answer composer (#291): retrieved evidence -> a cited, grounded answer.

    Given the question and the top retrieved sources (past Q&A / documents, each
    id-paired), compose an answer that draws ONLY from that evidence and cite the
    sources used. If the evidence does not actually answer the question, return
    ``grounded=False`` rather than inventing one — the decision to fall back to a
    human hand-off is the graph's, but the model must not hallucinate to avoid it.
    """

    def compose(self, question: str, evidence: Sequence[CitedEvidence]) -> SelfAnswerResult: ...


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


class BranchSource(Protocol):
    """Candidate -> employee lookup for the #83 branch constraint.

    :class:`~tekijin.data.repository.Repository` satisfies this; tests pass a
    lightweight fake so the node stays database-free. Only ``.branch`` is read.
    """

    def employees_by_ids(self, employee_ids: Sequence[int]) -> Mapping[int, Any]: ...
