"""Pure fixture-dict -> ORM-instance mappers.

Each ``map_*`` function turns one raw fixture record into an unsaved ORM
instance. Building an ORM object requires no database connection, so these are
unit-tested directly. Embeddings are always left ``None`` (computed later at
ingestion time). ``build_all`` assembles every table's rows in FK-safe order.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import DeclarativeBase

from tekijin.data.loaders import load_fixture, parse_date, parse_datetime
from tekijin.models import tables as t

# A mapper turns one raw fixture record into an unsaved ORM instance.
Mapper = Callable[[dict[str, Any]], DeclarativeBase]


def map_employee(r: dict[str, Any]) -> t.Employee:
    return t.Employee(
        id=r["id"],
        name=r["name"],
        email=r["email"],
        department=r.get("department"),
        section=r.get("section"),
        position=r.get("position"),
        branch=r.get("branch"),
        role=r.get("role"),
        hire_date=parse_date(r.get("hire_date")),
        department_history=r.get("department_history"),
    )


def map_profile(r: dict[str, Any]) -> t.EmployeeProfile:
    return t.EmployeeProfile(
        employee_id=r["employee_id"],
        description=r.get("description"),
        embedding=None,
        updated_at=parse_datetime(r.get("updated_at")),
    )


def map_certification(r: dict[str, Any]) -> t.Certification:
    return t.Certification(
        id=r["id"],
        employee_id=r["employee_id"],
        name=r["name"],
        acquired_at=parse_date(r.get("acquired_at")),
    )


def map_skill(r: dict[str, Any]) -> t.Skill:
    return t.Skill(
        id=r["id"],
        employee_id=r["employee_id"],
        topic=r["topic"],
        level=r.get("level"),
        source=r.get("source"),
    )


def map_project(r: dict[str, Any]) -> t.Project:
    return t.Project(
        id=r["id"],
        subject=r.get("subject"),
        client_company=r.get("client_company"),
        industry=r.get("industry"),
        company_size=r.get("company_size"),
        client_issue=r.get("client_issue"),
        product=r.get("product"),
        negotiation_count=r.get("negotiation_count"),
        status=r.get("status"),
        remarks=r.get("remarks"),
        start_date=parse_date(r.get("start_date")),
        end_date=parse_date(r.get("end_date")),
    )


def map_project_member(r: dict[str, Any]) -> t.ProjectMember:
    return t.ProjectMember(
        project_id=r["project_id"],
        employee_id=r["employee_id"],
        role=r["role"],
    )


def map_employee_chat(r: dict[str, Any]) -> t.EmployeeChatHistory:
    return t.EmployeeChatHistory(
        id=r["id"],
        sender_employee_id=r["sender_employee_id"],
        receiver_employee_id=r.get("receiver_employee_id"),
        channel=r.get("channel"),
        message=r.get("message"),
        sent_at=parse_datetime(r.get("sent_at")),
    )


def map_daily_report(r: dict[str, Any]) -> t.DailyReport:
    return t.DailyReport(
        id=r["id"],
        employee_id=r["employee_id"],
        report_date=parse_date(r.get("report_date")),
        content=r.get("content"),
        issue=r.get("issue"),
        created_at=parse_datetime(r.get("created_at")),
    )


def map_question(r: dict[str, Any]) -> t.Question:
    return t.Question(
        id=r["id"],
        asker_id=r["asker_id"],
        body=r.get("body"),
        topics=r.get("topics"),
        status=r.get("status"),
        created_at=parse_datetime(r.get("created_at")),
        embedding=None,
    )


def map_answer(r: dict[str, Any]) -> t.Answer:
    return t.Answer(
        id=r["id"],
        question_id=r["question_id"],
        responder_id=r["responder_id"],
        body=r.get("body"),
        created_at=parse_datetime(r.get("created_at")),
        embedding=None,
        reuse_count=r.get("reuse_count"),
        was_helpful=r.get("was_helpful"),
        topic=r.get("topic"),
    )


def map_document(r: dict[str, Any]) -> t.Document:
    return t.Document(
        id=r["id"],
        title=r.get("title"),
        body=r.get("body"),
        source=r.get("source"),
        updated_at=parse_datetime(r.get("updated_at")),
        embedding=None,
    )


# Ordered so that parents are inserted before their FK dependants.
_PLAN: tuple[tuple[str, str, Mapper], ...] = (
    ("employees", "employees", map_employee),
    ("projects", "projects", map_project),
    ("profiles", "profiles", map_profile),
    ("certifications", "certifications", map_certification),
    ("skills", "skills", map_skill),
    ("project_members", "project_members", map_project_member),
    ("employee_chat", "employee_chat", map_employee_chat),
    ("daily_reports", "daily_reports", map_daily_report),
    ("questions", "questions", map_question),
    ("answers", "answers", map_answer),
    ("documents", "documents", map_document),
)


def build_all(fixtures_dir: Path) -> list[tuple[str, list[DeclarativeBase]]]:
    """Load every fixture and map it to ORM rows, in FK-safe insert order.

    Returns a list of ``(logical_name, orm_rows)`` pairs so the seed can insert
    each group in order and report per-table counts.
    """

    result: list[tuple[str, list[DeclarativeBase]]] = []
    for logical_name, fixture_name, mapper in _PLAN:
        rows = [mapper(rec) for rec in load_fixture(fixtures_dir, fixture_name)]
        result.append((logical_name, rows))
    return result
