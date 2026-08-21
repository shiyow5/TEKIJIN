"""Read-oriented repository over the TEKIJIN schema.

A thin object that wraps a SQLAlchemy :class:`~sqlalchemy.orm.Session` and
exposes ``find``-style queries used by the search and scorer components. Every
method returns immutable :mod:`tekijin.data.dto` snapshots rather than live ORM
rows, keeping callers insulated from the session lifecycle and from accidental
mutation.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from tekijin.data.dto import (
    AnswerDTO,
    CertificationDTO,
    DocumentDTO,
    EmployeeDTO,
    ProfileDTO,
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
    Question,
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

    # -- documents -------------------------------------------------------- #
    def list_documents(self) -> list[DocumentDTO]:
        rows = self._session.scalars(select(Document).order_by(Document.id)).all()
        return [DocumentDTO.from_row(r) for r in rows]

    # -- projects --------------------------------------------------------- #
    def list_projects_with_members(self) -> list[ProjectWithMembersDTO]:
        stmt = select(Project).options(selectinload(Project.members)).order_by(Project.id)
        rows = self._session.scalars(stmt).all()
        return [ProjectWithMembersDTO.from_row(r) for r in rows]
