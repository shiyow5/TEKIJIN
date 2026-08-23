"""Pydantic v2 request / response / SSE-data contracts for the API boundary.

Every value crossing the HTTP boundary is validated through one of these models
(model-definition §4). ``asker_id`` is an ``int`` to match the DB. The SSE data
models mirror the events emitted by :mod:`tekijin.api.events`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Outcome = Literal["accepted", "declined"]

# session_id doubles as the ``/events/{session_id}`` path segment and the graph
# ``thread_id``; constrain it to path-safe characters (no ``/``) so a created
# session is always reachable over GET /events.
_SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]+$"


def format_employee_id(employee_id: int) -> str:
    """Render an internal int employee id as the external ``"E###"`` string form.

    Model-definition §163-170: the external contract uses zero-padded string ids
    (``"E017"``). Internal/DB stays int; this is applied only at the API boundary
    (recommend events), mirroring the ``"E###"`` asker_id we accept on input.
    """

    return f"E{int(employee_id):03d}"


def _coerce_asker_id(value: object) -> int:
    """Accept an int employee id or the spec's ``"E###"`` string form.

    The DB stores ``asker_id`` as an int; the product spec writes employee ids as
    ``"E200"``. We accept both at the boundary and normalise to int (the DB form),
    stripping an optional leading ``E``/``e``.
    """

    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise ValueError("asker_id must be an integer or 'E###' string")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("E", "e"):
            text = text[1:]
        if text.isdigit():
            return int(text)
    raise ValueError("asker_id must be an integer or 'E###' string")


# --------------------------------------------------------------------------- #
# requests
# --------------------------------------------------------------------------- #
class AskRequest(BaseModel):
    """Start (or restart) a question for a session."""

    asker_id: int
    question: str = Field(min_length=1)
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)

    @field_validator("asker_id", mode="before")
    @classmethod
    def _accept_e_prefixed_id(cls, value: object) -> int:
        # DB is int; ``"E200"`` (spec form) is accepted and converted here.
        return _coerce_asker_id(value)

    @field_validator("question")
    @classmethod
    def _trim_nonempty(cls, value: str) -> str:
        # Trim and reject whitespace-only questions at the boundary (422), so the
        # empty query never reaches C3 and surfaces as an SSE error.
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("question must not be blank")
        return trimmed


class ResumeRequest(BaseModel):
    """Resume a paused run: a responder ``outcome`` OR a clarification ``reply``.

    Exactly one of the two must be supplied — ``outcome`` answers a ``send``
    interrupt (accept/decline), ``reply`` answers a ``followup`` interrupt.
    """

    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    outcome: Outcome | None = None
    reply: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ResumeRequest:
        provided = [v for v in (self.outcome, self.reply) if v is not None]
        if len(provided) != 1:
            raise ValueError("provide exactly one of 'outcome' or 'reply'")
        if self.reply is not None and not self.reply.strip():
            raise ValueError("'reply' must be non-empty")
        return self

    @property
    def resume_value(self) -> str:
        return self.outcome if self.outcome is not None else (self.reply or "")


# --------------------------------------------------------------------------- #
# responses
# --------------------------------------------------------------------------- #
class AckResponse(BaseModel):
    """Acknowledgement for /ask and /answer (the stream flows over /events)."""

    session_id: str
    status: str


class EmployeeSummary(BaseModel):
    """One employee for the current-user switcher (id / name / dept).

    ``id`` is the external ``"E###"`` form (see :func:`format_employee_id`), the
    same shape accepted back as ``asker_id`` and used as the responder id for the
    inbox — so the frontend can round-trip the selected user without conversion.
    """

    id: str  # external "E###" form (see format_employee_id)
    name: str
    dept: str | None = None


class EmployeeListResponse(BaseModel):
    """Employee directory for the current-user switcher (no auth in the prototype)."""

    employees: list[EmployeeSummary] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# domain models (shared by SSE data and final response)
# --------------------------------------------------------------------------- #
class Reason(BaseModel):
    type: str
    detail: str


class Recommendation(BaseModel):
    person_id: str  # external "E###" form (see format_employee_id)
    name: str
    dept: str | None = None
    score: float
    confidence: str
    reasons: list[Reason] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# handoff (GET /handoff/{session_id}) — responder-facing view (product-spec 画面4)
# --------------------------------------------------------------------------- #
class HandoffAsker(BaseModel):
    """The asking employee, enriched for the responder-facing handoff view."""

    id: str  # external "E###" form (see format_employee_id)
    name: str | None = None
    dept: str | None = None


class HandoffResponse(BaseModel):
    """Responder-facing payload for a session paused at the ``send`` interrupt.

    Assembled from the durable checkpoint (question / asker / slots /
    recommendations / draft) plus DB aggregates (the responder's past-answer
    reuse). Read-only: fetching it does NOT advance the graph — the responder
    acts via ``POST /answer`` (outcome=accepted|declined).
    """

    session_id: str
    question: str
    asker: HandoffAsker
    topics: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    situation: str | None = None
    missing: list[str] = Field(default_factory=list)
    # The primary (handed-off) candidate — the person being asked — with the
    # selection reasons. ``None`` only in the degenerate no-candidate case.
    responder: Recommendation | None = None
    draft: str = ""
    reuse_count: int = 0
    helpful_answer_count: int = 0


# --------------------------------------------------------------------------- #
# SSE event data
# --------------------------------------------------------------------------- #
class UnderstoodData(BaseModel):
    topics: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    situation: str | None = None
    question_type: str | None = None
    confidence: float = 0.0


class FollowupData(BaseModel):
    question: str
    missing: list[str] = Field(default_factory=list)


class RouteData(BaseModel):
    route: str
    reason: str
    confidence: float


class RecommendData(BaseModel):
    recommendations: list[Recommendation] = Field(default_factory=list)


class DraftData(BaseModel):
    draft: str


class DoneData(BaseModel):
    status: str
    answer: str | None = None


class MessageData(BaseModel):
    status: str
    message: str


class ErrorData(BaseModel):
    error: str


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #
class ResponderLoad(BaseModel):
    employee_id: int
    name: str
    answer_count: int


class TopicCount(BaseModel):
    topic: str
    count: int


class OutcomeCounts(BaseModel):
    """Aggregate recommendation outcomes (no per-record enumeration)."""

    accepted: int = 0
    declined: int = 0
    pending: int = 0  # shown but not yet accepted/declined


class EvalSnapshot(BaseModel):
    """The latest stored offline-evaluation metrics (dashboard 推薦精度)."""

    top1_accuracy: float | None = None
    recall_at_3: float | None = None
    mrr: float | None = None
    route_accuracy: float | None = None
    created_at: str | None = None


class DashboardResponse(BaseModel):
    """Aggregate-only view (counts / distributions / ratios).

    Deliberately carries NO per-record listing (product-spec §241-251: the
    dashboard summarises usage, it is not a monitoring/audit log of individual
    recommendations).
    """

    total_employees: int
    total_questions: int
    total_answers: int
    recommendation_count: int
    recommendation_outcomes: OutcomeCounts = Field(default_factory=OutcomeCounts)
    acceptance_rate: float = 0.0  # accepted / (accepted + declined), 0 when none decided
    # product-spec 画面5 headline metrics.
    self_resolution_rate: float = 0.0  # 補助経路で解決した割合（route 記録から）
    avg_resolution_hours: float | None = None  # 平均解決時間（未解決なら None）
    top_responder_share: float = 0.0  # 上位1名集中率（負荷分散）
    latest_eval: EvalSnapshot | None = None  # 推薦精度（未計測なら None）
    answers_per_responder: list[ResponderLoad] = Field(default_factory=list)
    topic_distribution: list[TopicCount] = Field(default_factory=list)
