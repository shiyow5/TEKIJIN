"""Integration tests against a live PostgreSQL + pgvector database.

The ``engine`` / ``seed_counts`` / ``session`` fixtures (see ``conftest.py``)
use ``TEKIJIN_DATABASE_URL`` when set (CI) or an ephemeral ``pgserver`` instance
locally, and are skipped when neither is available.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from tekijin.config import get_settings
from tekijin.data.db import get_sessionmaker, session_scope
from tekijin.data.repository import Repository
from tekijin.data.seed import run_seed
from tekijin.models.tables import Answer, Employee, EmployeeProfile, Question


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def test_pgvector_extension_and_schema(engine) -> None:
    with engine.connect() as conn:
        ext = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar()
        assert ext == 1
        # A representative table from each layer exists.
        for table in ("employees", "answers", "person_topic_edges"):
            exists = conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar()
            assert exists is not None


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #
def test_seed_counts(seed_counts) -> None:
    assert seed_counts["employees"] == 40
    assert seed_counts["projects"] == 120
    assert seed_counts["project_members"] == 237
    assert seed_counts["daily_reports"] == 3070
    assert sum(seed_counts.values()) == 5993


def test_seed_leaves_embeddings_null(seed_counts, session) -> None:
    prof = session.scalars(select(EmployeeProfile).limit(1)).one()
    assert prof.embedding is None
    q = session.scalars(select(Question).limit(1)).one()
    assert q.embedding is None
    ans = session.scalars(select(Answer).limit(1)).one()
    assert ans.embedding is None


def test_seed_is_idempotent(engine, seed_counts) -> None:
    # Re-running truncates and re-inserts, converging on identical counts.
    again = run_seed(str(engine.url), get_settings().fixtures_dir)
    assert again == seed_counts


# --------------------------------------------------------------------------- #
# repository
# --------------------------------------------------------------------------- #
def test_list_and_get_employee(seed_counts, session) -> None:
    repo = Repository(session)
    employees = repo.list_employees()
    assert len(employees) == 40
    first = repo.get_employee(1)
    assert first is not None and first.id == 1 and first.branch
    assert repo.get_employee(999999) is None


def test_certifications_and_skills_fk_consistent(seed_counts, session) -> None:
    repo = Repository(session)
    certs = repo.certifications_for(1)
    assert certs and all(c.employee_id == 1 for c in certs)
    skills = repo.skills_for(1)
    assert all(s.employee_id == 1 for s in skills)


def test_answers_by_topic(seed_counts, session) -> None:
    repo = Repository(session)
    topic = "ネットワーク・VPN"
    answers = repo.answers_by_topic(topic)
    assert answers and all(a.topic == topic for a in answers)
    assert all(a.has_embedding is False for a in answers)


def test_questions_answers_documents(seed_counts, session) -> None:
    repo = Repository(session)
    questions = repo.list_questions()
    assert len(questions) == 150
    docs = repo.list_documents()
    assert len(docs) == 30
    # answers_for_question resolves a real FK link.
    linked = repo.answers_for_question(questions[0].id)
    assert all(a.question_id == questions[0].id for a in linked)


def test_projects_with_members(seed_counts, session) -> None:
    repo = Repository(session)
    projects = repo.list_projects_with_members()
    assert len(projects) == 120
    with_members = [p for p in projects if p.members]
    assert with_members
    roles = {m.role for p in with_members for m in p.members}
    assert roles <= {"lead", "member"}


def test_session_scope_rolls_back_on_error(engine, seed_counts) -> None:
    factory = get_sessionmaker(engine)
    with pytest.raises(RuntimeError), session_scope(factory) as sess:
        sess.add(Employee(id=999001, name="Ghost", email="g@x"))
        sess.flush()
        raise RuntimeError("boom")
    # The failed transaction must not have persisted the row.
    check = get_sessionmaker(engine)()
    try:
        assert check.get(Employee, 999001) is None
    finally:
        check.close()


def test_get_profile(seed_counts, session) -> None:
    repo = Repository(session)
    prof = repo.get_profile(1)
    assert prof is not None and prof.employee_id == 1
    assert prof.has_embedding is False
    assert repo.get_profile(999999) is None
