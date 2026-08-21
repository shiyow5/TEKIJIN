"""Read-oriented repository over the TEKIJIN schema.

A thin object that wraps a SQLAlchemy :class:`~sqlalchemy.orm.Session` and
exposes ``find``-style queries used by the search and scorer components. Every
method returns immutable :mod:`tekijin.data.dto` snapshots rather than live ORM
rows, keeping callers insulated from the session lifecycle and from accidental
mutation.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from tekijin.data.dto import (
    AnswerDTO,
    CertificationDTO,
    DocumentDTO,
    EmployeeDTO,
    ProfileDTO,
    ProjectMembershipDTO,
    ProjectWithMembersDTO,
    QuestionDTO,
    SkillDTO,
)
from tekijin.models.tables import (
    Answer,
    Certification,
    Document,
    Employee,
    EmployeeProfile,
    Project,
    ProjectMember,
    Question,
    Recommendation,
    Skill,
)


class Repository:
    """Read-only data access over an active session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # -- employees -------------------------------------------------------- #
    def list_employees(self) -> list[EmployeeDTO]:
        rows = self._session.scalars(select(Employee).order_by(Employee.id)).all()
        return [EmployeeDTO.from_row(r) for r in rows]

    def get_employee(self, employee_id: int) -> EmployeeDTO | None:
        row = self._session.get(Employee, employee_id)
        return EmployeeDTO.from_row(row) if row is not None else None

    def get_profile(self, employee_id: int) -> ProfileDTO | None:
        row = self._session.get(EmployeeProfile, employee_id)
        return ProfileDTO.from_row(row) if row is not None else None

    def list_profiles(self) -> list[ProfileDTO]:
        """Every employee profile, ordered by employee id.

        Used to build the BM25 profile corpus so people whose expertise terms
        live only in their self-description are reachable by lexical search.
        """

        stmt = select(EmployeeProfile).order_by(EmployeeProfile.employee_id)
        return [ProfileDTO.from_row(r) for r in self._session.scalars(stmt)]

    # -- expertise evidence ---------------------------------------------- #
    def certifications_for(self, employee_id: int) -> list[CertificationDTO]:
        stmt = (
            select(Certification)
            .where(Certification.employee_id == employee_id)
            .order_by(Certification.id)
        )
        return [CertificationDTO.from_row(r) for r in self._session.scalars(stmt)]

    def skills_for(self, employee_id: int) -> list[SkillDTO]:
        stmt = select(Skill).where(Skill.employee_id == employee_id).order_by(Skill.id)
        return [SkillDTO.from_row(r) for r in self._session.scalars(stmt)]

    # -- questions & answers --------------------------------------------- #
    def list_questions(self) -> list[QuestionDTO]:
        rows = self._session.scalars(select(Question).order_by(Question.id)).all()
        return [QuestionDTO.from_row(r) for r in rows]

    def answers_by_topic(self, topic: str) -> list[AnswerDTO]:
        """Answers relevant to ``topic``, matched two ways.

        The fixture-only ``answers.topic`` column is NULL for answers created at
        runtime, so the topic of a runtime answer lives on its linked question
        (``questions.topics`` is an array). This matches on either signal:
        ``answers.topic == topic`` OR ``topic = ANY(questions.topics)``.
        """

        stmt = (
            select(Answer)
            .join(Question, Answer.question_id == Question.id)
            .where(or_(Answer.topic == topic, Question.topics.any(topic)))
            .order_by(Answer.id)
        )
        return [AnswerDTO.from_row(r) for r in self._session.scalars(stmt)]

    def answers_for_question(self, question_id: str) -> list[AnswerDTO]:
        stmt = select(Answer).where(Answer.question_id == question_id).order_by(Answer.id)
        return [AnswerDTO.from_row(r) for r in self._session.scalars(stmt)]

    def list_answers(self) -> list[AnswerDTO]:
        """Every answer, ordered by id. Used to build the BM25 answer corpus."""

        rows = self._session.scalars(select(Answer).order_by(Answer.id)).all()
        return [AnswerDTO.from_row(r) for r in rows]

    # -- documents -------------------------------------------------------- #
    def list_documents(self) -> list[DocumentDTO]:
        rows = self._session.scalars(select(Document).order_by(Document.id)).all()
        return [DocumentDTO.from_row(r) for r in rows]

    # -- projects --------------------------------------------------------- #
    def list_projects_with_members(self) -> list[ProjectWithMembersDTO]:
        stmt = select(Project).options(selectinload(Project.members)).order_by(Project.id)
        rows = self._session.scalars(stmt).all()
        return [ProjectWithMembersDTO.from_row(r) for r in rows]

    def project_memberships_for(self, employee_id: int) -> list[ProjectMembershipDTO]:
        """Projects an employee is on (with role, product, dates) for scoring.

        Ordered by ``project_id`` so the result is deterministic.
        """

        stmt = (
            select(ProjectMember)
            .options(joinedload(ProjectMember.project))
            .where(ProjectMember.employee_id == employee_id)
            .order_by(ProjectMember.project_id)
        )
        return [ProjectMembershipDTO.from_member(m) for m in self._session.scalars(stmt)]

    # -- load (recency windows) ------------------------------------------ #
    def recent_recommendation_counts(
        self, since: dt.datetime, employee_ids: Sequence[int]
    ) -> dict[int, int]:
        """Per-employee count of recommendations created at/after ``since``.

        Feeds the scorer's ``load`` penalty (technical-spec §5: "直近7日の推薦件数",
        which is outcome-independent). ``declined`` recommendations ARE counted:
        a decline lowers only availability (余裕度), never expertise — the spec
        keeps declines out of the expertise evidence, not out of the load window.
        Scoped to ``employee_ids`` (the candidates being scored); an empty list
        yields ``{}`` without a query.
        """

        if not employee_ids:
            return {}
        stmt = (
            select(Recommendation.employee_id, func.count())
            .where(Recommendation.created_at >= since)
            .where(Recommendation.employee_id.in_(employee_ids))
            .group_by(Recommendation.employee_id)
        )
        return {employee_id: count for employee_id, count in self._session.execute(stmt)}

    def recent_answer_counts(
        self, since: dt.datetime, responder_ids: Sequence[int]
    ) -> dict[int, int]:
        """Per-responder count of answers created at/after ``since`` (load).

        Scoped to ``responder_ids``; an empty list yields ``{}`` without a query.
        """

        if not responder_ids:
            return {}
        stmt = (
            select(Answer.responder_id, func.count())
            .where(Answer.created_at >= since)
            .where(Answer.responder_id.in_(responder_ids))
            .group_by(Answer.responder_id)
        )
        return {responder_id: count for responder_id, count in self._session.execute(stmt)}
