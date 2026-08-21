"""Read-only data-transfer objects returned by the repository layer.

The repository never leaks live ORM instances to callers (search, scorers,
API). Instead it maps each row to one of these frozen dataclasses, so downstream
code gets immutable snapshots that are safe to pass around and cache. Every
``from_row`` builder is a pure function of an ORM instance and therefore
unit-testable without a database.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tekijin.models.tables import (
        Answer,
        Certification,
        Document,
        Employee,
        EmployeeProfile,
        Project,
        ProjectMember,
        Question,
        Skill,
    )


@dataclass(frozen=True, slots=True)
class EmployeeDTO:
    id: int
    name: str
    email: str
    department: str | None
    section: str | None
    position: str | None
    branch: str | None
    role: str | None
    hire_date: dt.date | None

    @classmethod
    def from_row(cls, row: Employee) -> EmployeeDTO:
        return cls(
            id=row.id,
            name=row.name,
            email=row.email,
            department=row.department,
            section=row.section,
            position=row.position,
            branch=row.branch,
            role=row.role,
            hire_date=row.hire_date,
        )


@dataclass(frozen=True, slots=True)
class ProfileDTO:
    employee_id: int
    description: str | None
    has_embedding: bool
    updated_at: dt.datetime | None

    @classmethod
    def from_row(cls, row: EmployeeProfile) -> ProfileDTO:
        return cls(
            employee_id=row.employee_id,
            description=row.description,
            has_embedding=row.embedding is not None,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True, slots=True)
class CertificationDTO:
    id: str
    employee_id: int
    name: str
    acquired_at: dt.date | None

    @classmethod
    def from_row(cls, row: Certification) -> CertificationDTO:
        return cls(
            id=row.id,
            employee_id=row.employee_id,
            name=row.name,
            acquired_at=row.acquired_at,
        )


@dataclass(frozen=True, slots=True)
class SkillDTO:
    id: str
    employee_id: int
    topic: str
    level: str | None
    source: str | None

    @classmethod
    def from_row(cls, row: Skill) -> SkillDTO:
        return cls(
            id=row.id,
            employee_id=row.employee_id,
            topic=row.topic,
            level=row.level,
            source=row.source,
        )


@dataclass(frozen=True, slots=True)
class QuestionDTO:
    id: str
    asker_id: int
    body: str | None
    topics: tuple[str, ...]
    status: str | None
    created_at: dt.datetime | None
    has_embedding: bool

    @classmethod
    def from_row(cls, row: Question) -> QuestionDTO:
        return cls(
            id=row.id,
            asker_id=row.asker_id,
            body=row.body,
            topics=tuple(row.topics or ()),
            status=row.status,
            created_at=row.created_at,
            has_embedding=row.embedding is not None,
        )


@dataclass(frozen=True, slots=True)
class AnswerDTO:
    id: str
    question_id: str
    responder_id: int
    body: str | None
    topic: str | None
    reuse_count: int | None
    was_helpful: bool | None
    created_at: dt.datetime | None
    has_embedding: bool

    @classmethod
    def from_row(cls, row: Answer) -> AnswerDTO:
        return cls(
            id=row.id,
            question_id=row.question_id,
            responder_id=row.responder_id,
            body=row.body,
            topic=row.topic,
            reuse_count=row.reuse_count,
            was_helpful=row.was_helpful,
            created_at=row.created_at,
            has_embedding=row.embedding is not None,
        )


@dataclass(frozen=True, slots=True)
class DocumentDTO:
    id: str
    title: str | None
    body: str | None
    source: str | None
    updated_at: dt.datetime | None
    has_embedding: bool

    @classmethod
    def from_row(cls, row: Document) -> DocumentDTO:
        return cls(
            id=row.id,
            title=row.title,
            body=row.body,
            source=row.source,
            updated_at=row.updated_at,
            has_embedding=row.embedding is not None,
        )


@dataclass(frozen=True, slots=True)
class MemberDTO:
    employee_id: int
    role: str


@dataclass(frozen=True, slots=True)
class ProjectWithMembersDTO:
    id: int
    subject: str | None
    client_company: str | None
    industry: str | None
    status: str | None
    members: tuple[MemberDTO, ...]

    @classmethod
    def from_row(cls, row: Project) -> ProjectWithMembersDTO:
        members = tuple(MemberDTO(employee_id=m.employee_id, role=m.role) for m in row.members)
        return cls(
            id=row.id,
            subject=row.subject,
            client_company=row.client_company,
            industry=row.industry,
            status=row.status,
            members=members,
        )


@dataclass(frozen=True, slots=True)
class ProjectMembershipDTO:
    """One employee's membership in a project, with the project's fields.

    Built for the C6 scorer: ``role`` drives the evidence base_score
    (lead 0.8 / member 0.5), ``product`` drives topic matching, and the dates
    drive recency.
    """

    project_id: int
    employee_id: int
    role: str
    product: str | None
    industry: str | None
    subject: str | None
    status: str | None
    start_date: dt.date | None
    end_date: dt.date | None

    @classmethod
    def from_member(cls, member: ProjectMember) -> ProjectMembershipDTO:
        project = member.project
        return cls(
            project_id=member.project_id,
            employee_id=member.employee_id,
            role=member.role,
            product=project.product,
            industry=project.industry,
            subject=project.subject,
            status=project.status,
            start_date=project.start_date,
            end_date=project.end_date,
        )
