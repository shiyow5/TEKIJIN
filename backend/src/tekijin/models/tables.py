"""SQLAlchemy 2.0 ORM table definitions for the TEKIJIN schema.

The schema follows ``docs/specs/db-schema.md`` and is organised in three layers:

* **A. Input data** — employees, profiles, chat, daily reports, projects.
* **B. Runtime** — certifications, skills, questions, answers, recommendations,
  events, project members, documents.
* **C. Expertise graph** — person/topic edges and their evidence.

``embedding`` columns use :class:`pgvector.sqlalchemy.Vector`; they are
``nullable`` because vectors are computed at ingestion time (a later component),
so fixtures seed the rows with ``NULL`` embeddings.

Every foreign key points back at :class:`Employee` (the "who"), matching the ER
diagram. Primary-key types mirror the synthetic fixtures verbatim: integer keys
for employees/projects/chat/reports, string keys for the id-prefixed entities
(``cert_0001``, ``q_0001`` …).
"""

from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tekijin.config import get_settings
from tekijin.data.db import Base

# Width of every pgvector column, taken from settings so the schema and the
# embedding model can never drift apart.
EMBEDDING_DIM: int = get_settings().embedding_dim


# --------------------------------------------------------------------------- #
# A. Input data layer
# --------------------------------------------------------------------------- #
class Employee(Base):
    """Employee master record. The "who" every other table refers to."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    department: Mapped[str | None] = mapped_column(String(255))
    section: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[str | None] = mapped_column(String(255))
    branch: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(255))
    hire_date: Mapped[dt.date | None] = mapped_column(Date)
    department_history: Mapped[list | None] = mapped_column(JSONB)


class EmployeeProfile(Base):
    """Free-text self-description of an employee (one row per employee)."""

    __tablename__ = "employee_profiles"

    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), primary_key=True, index=True
    )
    description: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class AiChatHistory(Base):
    """AI <-> employee conversation log (one row per message)."""

    __tablename__ = "ai_chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    speaker: Mapped[str] = mapped_column(String(32))
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class EmployeeChatHistory(Base):
    """Employee-to-employee chat log (Slack/Teams style)."""

    __tablename__ = "employee_chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sender_employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    receiver_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    channel: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class DailyReport(Base):
    """Daily report submitted by an employee."""

    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    report_date: Mapped[dt.date | None] = mapped_column(Date)
    content: Mapped[str | None] = mapped_column(Text)
    issue: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class Project(Base):
    """Sales/consulting project. Membership lives in ``project_members``."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str | None] = mapped_column(Text)
    client_company: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(255))
    company_size: Mapped[str | None] = mapped_column(String(255))
    client_issue: Mapped[str | None] = mapped_column(Text)
    product: Mapped[str | None] = mapped_column(String(255))
    negotiation_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(64))
    remarks: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[dt.date | None] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)

    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


# --------------------------------------------------------------------------- #
# B. Runtime layer
# --------------------------------------------------------------------------- #
class Certification(Base):
    """Certification held by an employee (strongest evidence, base_score 0.6)."""

    __tablename__ = "certifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    acquired_at: Mapped[dt.date | None] = mapped_column(Date)


class Skill(Base):
    """Self-declared / inferred skill (base_score 0.3 for ``self``)."""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    topic: Mapped[str] = mapped_column(String(255))
    level: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(64))


class Question(Base):
    """A question asked by an employee (the "asking" side)."""

    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asker_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    body: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    status: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


class Answer(Base):
    """An answer to a question (fuel for reuse path F-10 and learning)."""

    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), index=True)
    responder_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    reuse_count: Mapped[int | None] = mapped_column(Integer)
    was_helpful: Mapped[bool | None] = mapped_column(Boolean)
    # Not in the base ER but present in fixtures; drives ``answers_by_topic``.
    topic: Mapped[str | None] = mapped_column(String(255), index=True)


class Recommendation(Base):
    """Recommendation result and its outcome (the core of learning)."""

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    reasons: Mapped[dict | None] = mapped_column(JSONB)
    outcome: Mapped[str | None] = mapped_column(String(32))


class Event(Base):
    """Per-stage measurement row (p50/p95 latency KPI)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    stage: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    meta: Mapped[dict | None] = mapped_column(JSONB)


class ProjectMember(Base):
    """Project membership with role (lead 0.8 / member 0.5).

    PK is ``(project_id, employee_id)`` — an employee holds exactly one role per
    project, so ``role`` is a plain column, not part of the key. Keeping ``role``
    out of the PK prevents the same person being both lead and member on one
    project (which would double-count evidence in the C-layer graph).
    """

    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint("role IN ('lead', 'member')", name="ck_project_members_role"),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(32))

    project: Mapped[Project] = relationship(back_populates="members")


class Document(Base):
    """Internal document (demotion path, low priority)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


# --------------------------------------------------------------------------- #
# C. Expertise graph layer
# --------------------------------------------------------------------------- #
class PersonTopicEdge(Base):
    """Weighted person x topic expertise edge (recomputable from evidence)."""

    __tablename__ = "person_topic_edges"

    person_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), primary_key=True, index=True)
    topic_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    weight: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_count: Mapped[int | None] = mapped_column(Integer)
    last_updated: Mapped[dt.datetime | None] = mapped_column(DateTime)


class Evidence(Base):
    """A single piece of evidence backing an expertise edge."""

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    topic_id: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str | None] = mapped_column(String(32))
    base_score: Mapped[float | None] = mapped_column(Float)
    weight_contrib: Mapped[float | None] = mapped_column(Float)
    ts: Mapped[dt.datetime | None] = mapped_column(DateTime)


__all__ = [
    "EMBEDDING_DIM",
    "Employee",
    "EmployeeProfile",
    "AiChatHistory",
    "EmployeeChatHistory",
    "DailyReport",
    "Project",
    "Certification",
    "Skill",
    "Question",
    "Answer",
    "Recommendation",
    "Event",
    "ProjectMember",
    "Document",
    "PersonTopicEdge",
    "Evidence",
]
