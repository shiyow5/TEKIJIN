"""Integration tests against a live PostgreSQL + pgvector database.

The ``engine`` / ``seed_counts`` / ``session`` fixtures (see ``conftest.py``)
use ``TEKIJIN_DATABASE_URL`` when set (CI) or an ephemeral ``pgserver`` instance
locally, and are skipped when neither is available.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import event, select, text

from tekijin.config import get_settings
from tekijin.data.db import get_engine, get_sessionmaker, session_scope
from tekijin.data.history import recent_questions_for_asker
from tekijin.data.inbox import pending_handoffs_for_responder
from tekijin.data.repository import Repository
from tekijin.data.seed import _apply_schema_upgrades, apply_migrations, run_seed
from tekijin.models.tables import (
    Answer,
    Employee,
    EmployeeProfile,
    ProjectMember,
    Question,
    Recommendation,
)


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


def test_indexes_exist_on_filtered_columns(engine) -> None:
    # Columns the repository filters on must be indexed.
    expected = {
        ("certifications", "employee_id"),
        ("skills", "employee_id"),
        ("answers", "topic"),
        ("answers", "question_id"),
        ("answers", "responder_id"),
        ("questions", "asker_id"),
        ("project_members", "employee_id"),
        ("daily_reports", "employee_id"),
        ("employee_profiles", "employee_id"),
        ("evidence", "person_id"),
        ("person_topic_edges", "person_id"),
        ("recommendations", "created_at"),  # scorer's 7-day `load` recency window
        ("questions", "topics"),  # GIN index for topic = ANY(questions.topics)
    }
    with engine.connect() as conn:
        # Scope to the current schema (the tests run inside the unique test schema).
        rows = conn.execute(
            text(
                "SELECT t.relname AS table_name, a.attname AS column_name "
                "FROM pg_index i "
                "JOIN pg_class t ON t.oid = i.indrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "JOIN pg_attribute a "
                "  ON a.attrelid = t.oid AND a.attnum = ANY(i.indkey) "
                "WHERE n.nspname = current_schema()"
            )
        ).all()
    indexed = {(r.table_name, r.column_name) for r in rows}
    missing = expected - indexed
    assert not missing, f"missing indexes: {missing}"


def test_recommendation_created_at_server_default(seed_counts, session) -> None:
    # created_at is stamped by the DB (server_default=now); the scorer's 7-day
    # `load` window depends on it.
    rec = Recommendation(question_id="q_0001", employee_id=3, rank=1, score=0.9)
    session.add(rec)
    session.flush()
    session.refresh(rec)
    assert rec.created_at is not None


def test_project_members_role_check_constraint(engine, seed_counts) -> None:
    # The CHECK constraint must reject roles outside {lead, member}.
    factory = get_sessionmaker(engine)
    with (
        pytest.raises(Exception),  # noqa: B017 - IntegrityError from CHECK
        session_scope(factory) as sess,
    ):
        sess.add(ProjectMember(project_id=1, employee_id=1, role="observer"))


# --------------------------------------------------------------------------- #
# responder inbox (#123)
# --------------------------------------------------------------------------- #
def _add_pending_question(
    session, *, qid, session_id, asker_id, responder_id, rank=1, outcome=None
):
    session.add(
        Question(
            id=qid,
            asker_id=asker_id,
            body=f"{qid} の本文",
            topics=["ネットワーク"],
            status="open",
            session_id=session_id,
        )
    )
    session.add(
        Recommendation(
            question_id=qid, employee_id=responder_id, rank=rank, score=0.9, outcome=outcome
        )
    )


def test_pending_handoffs_lists_only_pending_rank1_for_the_responder(seed_counts, session) -> None:
    # A pending rank-1 rec for responder 5 with a session id -> in the inbox.
    _add_pending_question(session, qid="api_ibx_1", session_id="s-1", asker_id=10, responder_id=5)
    # Excluded: already answered (outcome set), not rank 1, another responder,
    # and a pending rec whose question has no session id (seeded history).
    _add_pending_question(
        session, qid="api_ibx_2", session_id="s-2", asker_id=10, responder_id=5, outcome="accepted"
    )
    _add_pending_question(
        session, qid="api_ibx_3", session_id="s-3", asker_id=10, responder_id=5, rank=2
    )
    _add_pending_question(session, qid="api_ibx_4", session_id="s-4", asker_id=10, responder_id=6)
    _add_pending_question(session, qid="api_ibx_5", session_id=None, asker_id=10, responder_id=5)
    session.flush()

    items = pending_handoffs_for_responder(session, 5)
    assert [i["session_id"] for i in items] == ["s-1"]
    item = items[0]
    assert item["question_id"] == "api_ibx_1"
    assert item["question"] == "api_ibx_1 の本文"
    assert item["topics"] == ["ネットワーク"]
    assert item["asker_id"] == 10
    assert item["asker_name"]  # joined from employees
    assert item["created_at"] is not None


def test_pending_handoffs_dedupes_by_session_keeping_newest(seed_counts, session) -> None:
    # Two pending rank-1 recs for the same session -> a single inbox item.
    session.add(Question(id="api_ibx_dup", asker_id=10, body="重複", topics=[], session_id="s-dup"))
    session.add(Recommendation(question_id="api_ibx_dup", employee_id=7, rank=1, score=0.8))
    session.add(Recommendation(question_id="api_ibx_dup", employee_id=7, rank=1, score=0.9))
    session.flush()

    items = pending_handoffs_for_responder(session, 7)
    assert len(items) == 1
    assert items[0]["session_id"] == "s-dup"


def test_pending_handoffs_excludes_declined_and_includes_rerouted(seed_counts, session) -> None:
    # The decline→reroute shape on ONE question/session: responder 8 declined
    # (outcome set), and it was rerouted to responder 9 as a fresh rank-1 row
    # with a NULL outcome. Responder 8 must drop out; responder 9 must appear.
    session.add(
        Question(id="api_ibx_rr", asker_id=10, body="reroute", topics=[], session_id="s-rr")
    )
    session.add(
        Recommendation(
            question_id="api_ibx_rr", employee_id=8, rank=1, score=0.9, outcome="declined"
        )
    )
    session.add(Recommendation(question_id="api_ibx_rr", employee_id=9, rank=1, score=0.8))
    session.flush()

    assert pending_handoffs_for_responder(session, 8) == []
    assert [i["session_id"] for i in pending_handoffs_for_responder(session, 9)] == ["s-rr"]


def test_pending_handoffs_empty_for_responder_without_handoffs(seed_counts, session) -> None:
    assert pending_handoffs_for_responder(session, 1) == []


# --------------------------------------------------------------------------- #
# asker recent-questions history (#125)
# --------------------------------------------------------------------------- #
def test_recent_questions_orders_newest_first_and_resolves(seed_counts, session) -> None:
    # Far-future timestamps so these outrank any seeded questions for asker 11.
    base = dt.datetime(2099, 1, 1, 9, 0, 0)
    asker = 11
    session.add(
        Question(
            id="api_rh_1", asker_id=asker, body="Q1 open", topics=[], status="open", created_at=base
        )
    )
    session.add(
        Question(
            id="api_rh_2",
            asker_id=asker,
            body="Q2 accepted",
            topics=[],
            status="open",
            created_at=base + dt.timedelta(hours=1),
        )
    )
    session.add(
        Question(
            id="api_rh_3",
            asker_id=asker,
            body="Q3 answered",
            topics=[],
            status="answered",
            created_at=base + dt.timedelta(hours=2),
        )
    )
    # A question by a different asker must not leak in.
    session.add(
        Question(
            id="api_rh_x",
            asker_id=12,
            body="other",
            topics=[],
            status="open",
            created_at=base + dt.timedelta(hours=3),
        )
    )
    session.flush()  # questions exist before their FK-dependent rows

    # Q2 accepted by employee 5; Q3 answered (seeded-style) by employee 6.
    session.add(Recommendation(question_id="api_rh_2", employee_id=5, rank=1, outcome="accepted"))
    session.add(Answer(id="api_ra_3", question_id="api_rh_3", responder_id=6, body="ans"))
    session.flush()

    items = recent_questions_for_asker(session, asker, limit=10)
    assert [i["question_id"] for i in items[:3]] == ["api_rh_3", "api_rh_2", "api_rh_1"]
    q3, q2, q1 = items[0], items[1], items[2]
    assert q3["resolved"] is True and q3["responder_name"]  # first answerer
    assert q3["resolution"] == "person"
    assert q2["resolved"] is True and q2["responder_name"]  # accepting responder
    assert q2["resolution"] == "person"
    assert q1["resolved"] is False and q1["responder_name"] is None
    assert q1["resolution"] == "pending"
    assert all(i["question_id"] != "api_rh_x" for i in items)


def test_recent_questions_document_route_is_self_resolved(seed_counts, session) -> None:
    # A document-routed question has no person responder, but is self-resolved
    # (Question.route == "document") — it must not read as "取り次ぎ先を調整中" (#142).
    session.add(
        Question(
            id="api_rh_doc",
            asker_id=15,
            body="社内PCのセットアップ手順は?",
            topics=[],
            status="open",
            route="document",
            created_at=dt.datetime(2099, 4, 1, 9, 0, 0),
        )
    )
    session.flush()

    items = recent_questions_for_asker(session, 15)
    doc = next(i for i in items if i["question_id"] == "api_rh_doc")
    assert doc["resolution"] == "document"
    assert doc["resolved"] is True
    assert doc["responder_name"] is None  # no human took it


def test_recent_questions_accepted_responder_tiebreak_is_deterministic(
    seed_counts, session
) -> None:
    # If a question has more than one accepted rank-1 rec (decline+reroute edge),
    # the newest one wins deterministically (mirrors the inbox dedup).
    session.add(
        Question(
            id="api_rt",
            asker_id=14,
            body="tie",
            topics=[],
            status="open",
            created_at=dt.datetime(2099, 3, 1, 9, 0, 0),
        )
    )
    session.flush()
    session.add(Recommendation(question_id="api_rt", employee_id=5, rank=1, outcome="accepted"))
    session.add(Recommendation(question_id="api_rt", employee_id=6, rank=1, outcome="accepted"))
    session.flush()

    items = recent_questions_for_asker(session, 14)
    newest = session.get(Employee, 6)  # higher id → inserted last → newest
    assert items[0]["responder_name"] == newest.name


def test_recent_questions_respects_limit(seed_counts, session) -> None:
    base = dt.datetime(2099, 2, 1, 9, 0, 0)
    for i in range(7):
        session.add(
            Question(
                id=f"api_rl_{i}",
                asker_id=13,
                body=f"Q{i}",
                topics=[],
                status="open",
                created_at=base + dt.timedelta(minutes=i),
            )
        )
    session.flush()

    items = recent_questions_for_asker(session, 13, limit=5)
    assert len(items) == 5
    assert items[0]["question_id"] == "api_rl_6"  # newest


def test_recent_questions_empty_for_asker_without_questions(seed_counts, session) -> None:
    assert recent_questions_for_asker(session, 9999) == []


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #
def test_seed_counts(seed_counts) -> None:
    assert seed_counts["employees"] == 40
    assert seed_counts["projects"] == 120
    assert seed_counts["project_members"] == 237
    assert seed_counts["daily_reports"] == 3070
    # #51/#52 で skills が 58 -> 61 になった分、合計が 5993 -> 5996
    assert sum(seed_counts.values()) == 5996


def test_seed_leaves_embeddings_null(seed_counts, session) -> None:
    prof = session.scalars(select(EmployeeProfile).limit(1)).one()
    assert prof.embedding is None
    q = session.scalars(select(Question).limit(1)).one()
    assert q.embedding is None
    ans = session.scalars(select(Answer).limit(1)).one()
    assert ans.embedding is None


def test_seed_is_idempotent(engine, seed_counts) -> None:
    # Re-running truncates and re-inserts, converging on identical counts.
    # Pass the live engine (never str(engine.url), which masks the password).
    again = run_seed(engine=engine, fixtures_dir=get_settings().fixtures_dir)
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
    assert answers
    assert all(a.has_embedding is False for a in answers)
    # Every returned answer is relevant either via its own topic column or via
    # the topic array of its linked question.
    q_topics = {q.id: q.topics for q in repo.list_questions()}
    for a in answers:
        assert a.topic == topic or topic in q_topics.get(a.question_id, ())


def test_answers_by_topics_single_query(seed_counts, session) -> None:
    repo = Repository(session)
    topics = ["ネットワーク・VPN", "セキュリティ"]
    answers = repo.answers_by_topics(topics)
    assert answers
    q_topics = {q.id: q.topics for q in repo.list_questions()}
    for a in answers:
        # Strict rule: own topic is one of `topics`, OR own topic is NULL and the
        # question's topics overlap `topics`.
        if a.topic is not None:
            assert a.topic in topics
        else:
            assert set(q_topics.get(a.question_id, ())) & set(topics)
    # Ordered by id (deterministic) and de-duplicated.
    ids = [a.id for a in answers]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    # Empty topics -> empty, no query.
    assert repo.answers_by_topics([]) == []


def test_answers_by_topics_excludes_other_subtopic(seed_counts, session) -> None:
    # An answer tagged for a DIFFERENT subtopic than requested is excluded, even
    # if its question's topics array overlaps the requested set.
    session.add(
        Answer(
            id="ans_other_subtopic",
            question_id="q_0001",  # its questions.topics contains ネットワーク・VPN
            responder_id=3,
            body="tagged for a different subtopic",
            topic="セキュリティ",  # own topic is NOT the requested one
        )
    )
    session.flush()
    repo = Repository(session)
    found = {a.id for a in repo.answers_by_topics(["ネットワーク・VPN"])}
    assert "ans_other_subtopic" not in found  # excluded (own topic != requested)


def test_answers_by_topic_matches_via_question_topics(seed_counts, session) -> None:
    # A runtime-style answer has topic=NULL; its topic lives on the question.
    topic = "ネットワーク・VPN"
    runtime = Answer(
        id="ans_runtime_test",
        question_id="q_0001",  # its questions.topics contains `topic`
        responder_id=3,
        body="runtime answer without a topic column",
        topic=None,
    )
    session.add(runtime)
    session.flush()  # visible in-session; not committed (rolled back on close)
    session.refresh(runtime)
    # created_at was omitted above; the DB server_default must stamp it.
    assert runtime.created_at is not None

    repo = Repository(session)
    found = {a.id for a in repo.answers_by_topic(topic)}
    assert "ans_runtime_test" in found


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


# --------------------------------------------------------------------------- #
# schema upgrades (migration path for an existing / older database)
# --------------------------------------------------------------------------- #
def test_apply_schema_upgrades_migrates_old_db(database_url: str) -> None:
    """`_apply_schema_upgrades` brings an OLD database up to the current schema.

    Simulates a DB created before #63 (embedding columns ``vector(1024)``,
    ``questions`` without a ``route`` column) and asserts the guarded DDL:
    widens every embedding column to ``vector(2048)`` (dropping stale, wrong-model
    embeddings via ``USING NULL``), adds ``questions.route``, and is idempotent.
    """

    schema = "mig_upgrade_test"
    tables = ("employee_profiles", "questions", "answers", "documents")

    admin = get_engine(database_url)
    try:
        with admin.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {schema}"))
    finally:
        admin.dispose()

    eng = get_engine(database_url)

    @event.listens_for(eng, "connect")
    def _set_search_path(dbapi_conn, _record):  # pragma: no cover - driver callback
        with dbapi_conn.cursor() as cur:
            cur.execute(f"SET search_path TO {schema}, public")

    def embedding_type(conn, table: str) -> str:
        return conn.execute(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = to_regclass(:t) AND attname = 'embedding'"
            ),
            {"t": table},
        ).scalar()

    try:
        # Old DB: 1024-d embedding columns; questions has no route column.
        with eng.begin() as conn:
            for table in tables:
                conn.execute(
                    text(f"CREATE TABLE {table} (id int primary key, embedding vector(1024))")
                )
            stale = "[" + ",".join(["0.01"] * 1024) + "]"
            conn.execute(text(f"INSERT INTO documents (id, embedding) VALUES (1, '{stale}')"))

        _apply_schema_upgrades(eng)

        with eng.connect() as conn:
            for table in tables:
                assert embedding_type(conn, table) == "vector(2048)"
            # Stale (wrong-model) embedding was dropped, not left at the old width.
            assert (
                conn.execute(text("SELECT embedding FROM documents WHERE id = 1")).scalar() is None
            )
            has_route = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema = :s "
                    "AND table_name = 'questions' AND column_name = 'route'"
                ),
                {"s": schema},
            ).scalar()
            assert has_route == 1

        # Idempotent: a second run is a no-op (still 2048, no error).
        _apply_schema_upgrades(eng)
        with eng.connect() as conn:
            assert embedding_type(conn, "documents") == "vector(2048)"
    finally:
        eng.dispose()
        admin = get_engine(database_url)
        try:
            with admin.begin() as conn:
                conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        finally:
            admin.dispose()


def test_apply_migrations_is_non_destructive(engine, seed_counts) -> None:
    """`apply_migrations` updates the schema without truncating existing rows.

    Unlike ``run_seed`` (which truncates + reloads), the data-preserving deploy
    path must keep rows intact and be idempotent.
    """

    factory = get_sessionmaker(engine)
    with factory() as sess:
        before = len(Repository(sess).list_employees())
    apply_migrations(engine=engine)  # must NOT truncate
    with factory() as sess:
        after = len(Repository(sess).list_employees())
    assert before == after == 40
