"""Integration tests for the C6 scorer against live PostgreSQL + pgvector.

Uses the shared ``engine`` / ``seed_counts`` / ``session`` fixtures (conftest):
CI's pgvector service via ``TEKIJIN_DATABASE_URL`` or an ephemeral ``pgserver``
locally, skipped when neither is available.

Evidence is isolated with a SYNTHETIC topic that matches nothing in the seed
(no skill, cert, product, or answer maps to it), so every candidate starts from
zero and the only evidence is what each test inserts. ``now`` is injected, so the
7-day load window and recency are fully deterministic. Inserted rows are flushed
(visible in-session) but never committed — each test rolls back on close.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from tekijin.data.repository import Repository
from tekijin.models.tables import (
    Answer,
    Certification,
    Employee,
    Project,
    ProjectMember,
    Question,
    Recommendation,
    Skill,
)
from tekijin.scorer import ExpertiseScorer

# Well past the newest seed answer (2026-08-21), so no seeded activity falls in
# the 7-day load window: load is then driven purely by rows each test inserts.
NOW = dt.datetime(2026, 9, 15, 12, 0, 0)
TOPIC = "__SCORER_TEST_TOPIC__"  # matches nothing in the seed


# --------------------------------------------------------------------------- #
# insert helpers (flushed, not committed)
# --------------------------------------------------------------------------- #
def _add_question(session, qid: str, asker_id: int = 1) -> None:
    session.add(Question(id=qid, asker_id=asker_id, body="q", topics=[TOPIC], status="open"))
    session.flush()


def _add_answer(
    session,
    aid: str,
    qid: str,
    responder_id: int,
    *,
    helpful: bool,
    reuse: int,
    created: dt.datetime,
) -> None:
    session.add(
        Answer(
            id=aid,
            question_id=qid,
            responder_id=responder_id,
            body="a",
            topic=TOPIC,
            reuse_count=reuse,
            was_helpful=helpful,
            created_at=created,
        )
    )
    session.flush()


def _add_skill(session, sid: str, employee_id: int) -> None:
    session.add(Skill(id=sid, employee_id=employee_id, topic=TOPIC, level="中級", source="self"))
    session.flush()


def _add_recommendation(
    session, employee_id: int, *, created: dt.datetime, outcome: str | None, qid: str
) -> None:
    session.add(
        Recommendation(
            question_id=qid,
            employee_id=employee_id,
            rank=1,
            score=0.5,
            outcome=outcome,
            created_at=created,
        )
    )
    session.flush()


def _scores_by_person(result: dict) -> dict[int, float]:
    return {r["person_id"]: r["score"] for r in result["recommendations"]}


# --------------------------------------------------------------------------- #
# output shape / top_k
# --------------------------------------------------------------------------- #
def test_rank_output_shape_and_top_k(seed_counts, session) -> None:
    _add_question(session, "q_shape")
    # Five candidates with differing strengths (skill + varying helpful answers).
    for i, emp in enumerate([1, 2, 3, 4, 5]):
        _add_skill(session, f"sk_shape_{emp}", emp)
        for j in range(i):  # emp 1 gets 0, emp 5 gets 4 helpful answers
            _add_answer(
                session, f"a_shape_{emp}_{j}", "q_shape", emp, helpful=True, reuse=1, created=NOW
            )

    scorer = ExpertiseScorer(Repository(session))
    result = scorer.rank(TOPIC, [1, 2, 3, 4, 5], asker_id=None, now=NOW, top_k=3)

    recs = result["recommendations"]
    assert set(result) == {"recommendations"}
    assert len(recs) == 3
    for rec in recs:
        assert set(rec) == {"person_id", "name", "dept", "score", "confidence", "reasons"}
        assert isinstance(rec["person_id"], int)
        assert isinstance(rec["score"], float)
        assert rec["confidence"] in {"高", "中", "低"}
        assert rec["reasons"] and all(set(r) == {"type", "detail"} for r in rec["reasons"])
    # Sorted by descending score.
    scores = [r["score"] for r in recs]
    assert scores == sorted(scores, reverse=True)
    # The strongest candidate (emp 5, most helpful answers) leads.
    assert recs[0]["person_id"] == 5


# --------------------------------------------------------------------------- #
# expert ranks above a bare candidate
# --------------------------------------------------------------------------- #
def test_expert_outranks_thin_candidate(seed_counts, session) -> None:
    _add_question(session, "q_exp")
    # Expert: skill + three helpful, reused answers.
    _add_skill(session, "sk_exp", 1)
    for j in range(3):
        _add_answer(session, f"a_exp_{j}", "q_exp", 1, helpful=True, reuse=2, created=NOW)
    # Thin candidate: only a self-declared skill.
    _add_skill(session, "sk_thin", 2)

    scorer = ExpertiseScorer(Repository(session))
    result = scorer.rank(TOPIC, [2, 1], asker_id=None, now=NOW, top_k=3)

    assert result["recommendations"][0]["person_id"] == 1
    scores = _scores_by_person(result)
    assert scores[1] > scores[2]
    # The expert's reasons mention the answers evidence.
    expert = result["recommendations"][0]
    assert any(r["type"] == "answers" for r in expert["reasons"])
    assert expert["confidence"] in {"高", "中"}


# --------------------------------------------------------------------------- #
# load penalty
# --------------------------------------------------------------------------- #
def test_high_load_lowers_score(seed_counts, session) -> None:
    _add_question(session, "q_load")
    # Identical evidence for both candidates.
    for emp in (1, 2):
        _add_skill(session, f"sk_load_{emp}", emp)
        _add_answer(session, f"a_load_{emp}", "q_load", emp, helpful=True, reuse=1, created=NOW)
    # emp 2 is busy this week (8 recent recommendations).
    for _ in range(8):
        _add_recommendation(
            session, 2, created=NOW - dt.timedelta(days=1), outcome="accepted", qid="q_load"
        )

    scorer = ExpertiseScorer(Repository(session))
    result = scorer.rank(TOPIC, [1, 2], asker_id=None, now=NOW, top_k=3)

    scores = _scores_by_person(result)
    assert scores[1] > scores[2]  # load drags emp 2 below the identical emp 1
    assert result["recommendations"][0]["person_id"] == 1


def test_declined_recommendations_do_not_count_as_load(seed_counts, session) -> None:
    _add_question(session, "q_decl")
    for emp in (1, 2):
        _add_skill(session, f"sk_decl_{emp}", emp)
    # emp 1 has many DECLINED recs this week; declines must not raise load.
    for _ in range(8):
        _add_recommendation(
            session, 1, created=NOW - dt.timedelta(days=1), outcome="declined", qid="q_decl"
        )

    scorer = ExpertiseScorer(Repository(session))
    result = scorer.rank(TOPIC, [1, 2], asker_id=None, now=NOW, top_k=3)

    scores = _scores_by_person(result)
    # Identical evidence + no counted load -> equal scores, tiebreak by person_id.
    assert scores[1] == scores[2]
    assert [r["person_id"] for r in result["recommendations"]] == [1, 2]


def test_old_recommendations_outside_window_ignored(seed_counts, session) -> None:
    _add_question(session, "q_old")
    for emp in (1, 2):
        _add_skill(session, f"sk_old_{emp}", emp)
    # emp 2's recs are 10 days old — outside the 7-day load window.
    for _ in range(8):
        _add_recommendation(
            session, 2, created=NOW - dt.timedelta(days=10), outcome="accepted", qid="q_old"
        )

    scorer = ExpertiseScorer(Repository(session))
    scores = _scores_by_person(scorer.rank(TOPIC, [1, 2], asker_id=None, now=NOW, top_k=3))
    assert scores[1] == scores[2]  # old load ignored -> tie


# --------------------------------------------------------------------------- #
# candidate scoping / unknown ids
# --------------------------------------------------------------------------- #
def test_only_input_candidates_are_scored(seed_counts, session) -> None:
    _add_question(session, "q_scope")
    for emp in (1, 2):
        _add_skill(session, f"sk_scope_{emp}", emp)

    scorer = ExpertiseScorer(Repository(session))
    result = scorer.rank(TOPIC, [1, 2, 999999], asker_id=None, now=NOW, top_k=3)

    person_ids = {r["person_id"] for r in result["recommendations"]}
    assert person_ids <= {1, 2}  # unknown id 999999 dropped; nobody outside input
    assert 999999 not in person_ids


# --------------------------------------------------------------------------- #
# proximity
# --------------------------------------------------------------------------- #
def test_same_branch_candidate_ranks_higher(seed_counts, session) -> None:
    # Pick an asker and two candidates: one in the asker's branch, one elsewhere.
    employees = session.scalars(select(Employee).order_by(Employee.id)).all()
    asker = employees[0]
    same = next(e for e in employees[1:] if e.branch == asker.branch)
    diff = next(e for e in employees[1:] if e.branch != asker.branch)

    _add_question(session, "q_prox", asker_id=asker.id)
    for emp in (same.id, diff.id):
        _add_skill(session, f"sk_prox_{emp}", emp)  # identical evidence

    scorer = ExpertiseScorer(Repository(session))
    result = scorer.rank(TOPIC, [diff.id, same.id], asker_id=asker.id, now=NOW, top_k=3)

    assert result["recommendations"][0]["person_id"] == same.id
    scores = _scores_by_person(result)
    assert scores[same.id] > scores[diff.id]
    # The proximity reason reflects the same-branch match.
    top_reasons = {r["type"] for r in result["recommendations"][0]["reasons"]}
    assert "proximity" in top_reasons


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_rank_is_deterministic(seed_counts, session) -> None:
    _add_question(session, "q_det")
    for emp in (1, 2, 3):
        _add_skill(session, f"sk_det_{emp}", emp)
        _add_answer(session, f"a_det_{emp}", "q_det", emp, helpful=True, reuse=1, created=NOW)

    scorer = ExpertiseScorer(Repository(session))
    first = scorer.rank(TOPIC, [1, 2, 3], asker_id=None, now=NOW, top_k=3)
    second = scorer.rank(TOPIC, [1, 2, 3], asker_id=None, now=NOW, top_k=3)
    assert first == second


# --------------------------------------------------------------------------- #
# reasons decompose cert + project evidence (real topic with matching maps)
# --------------------------------------------------------------------------- #
def test_reasons_include_cert_and_project(seed_counts, session) -> None:
    real_topic = "CRM・営業支援"
    emp = 7
    # A certification whose name maps to the topic (中小企業診断士 -> CRM・営業支援).
    session.add(
        Certification(
            id="cert_scorer_test", employee_id=emp, name="中小企業診断士", acquired_at=None
        )
    )
    # A project whose product maps to the topic; emp is the lead.
    session.add(
        Project(id=990001, subject="s", product="CRM導入支援", start_date=None, end_date=None)
    )
    session.flush()
    session.add(ProjectMember(project_id=990001, employee_id=emp, role="lead"))
    session.flush()

    scorer = ExpertiseScorer(Repository(session))
    result = scorer.rank(real_topic, [emp], asker_id=None, now=NOW, top_k=3)

    rec = result["recommendations"][0]
    assert rec["person_id"] == emp
    reason_types = {r["type"] for r in rec["reasons"]}
    assert {"cert", "project"} <= reason_types
    cert_reason = next(r for r in rec["reasons"] if r["type"] == "cert")
    assert "中小企業診断士" in cert_reason["detail"]
    project_reason = next(r for r in rec["reasons"] if r["type"] == "project")
    assert "案件" in project_reason["detail"]
