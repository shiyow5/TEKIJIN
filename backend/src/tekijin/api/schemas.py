"""Pydantic v2 request / response / SSE-data contracts for the API boundary.

Every value crossing the HTTP boundary is validated through one of these models
(model-definition §4). ``asker_id`` is an ``int`` to match the DB. The SSE data
models mirror the events emitted by :mod:`tekijin.api.events`.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Outcome = Literal["accepted", "declined"]


# --------------------------------------------------------------------------- #
# requests
# --------------------------------------------------------------------------- #
class AskRequest(BaseModel):
    """Start (or restart) a question for a session."""

    asker_id: int
    question: str = Field(min_length=1)
    session_id: str = Field(min_length=1)

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

    session_id: str = Field(min_length=1)
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


# --------------------------------------------------------------------------- #
# domain models (shared by SSE data and final response)
# --------------------------------------------------------------------------- #
class Reason(BaseModel):
    type: str
    detail: str


class Recommendation(BaseModel):
    person_id: int
    name: str
    dept: str | None = None
    score: float
    confidence: str
    reasons: list[Reason] = Field(default_factory=list)


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


class RecentRecommendation(BaseModel):
    question_id: str
    employee_id: int
    name: str
    score: float | None = None
    outcome: str | None = None
    created_at: dt.datetime | None = None


class DashboardResponse(BaseModel):
    total_employees: int
    total_questions: int
    total_answers: int
    recommendation_count: int
    answers_per_responder: list[ResponderLoad] = Field(default_factory=list)
    topic_distribution: list[TopicCount] = Field(default_factory=list)
    recent_recommendations: list[RecentRecommendation] = Field(default_factory=list)
