"""Integration tests for the API against live PostgreSQL + pgvector (pgserver/CI).

The agent runs on the deterministic stub LLM nodes and (usually) injected fake C4/C6
so the SSE flow is reproducible; C6/dashboard read the real seeded DB. No network,
no model download. ``now`` is injected for determinism.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from tekijin.agent.stubs import KeywordIntentModel, RuleSufficiencyModel, TemplateDraftModel
from tekijin.api.service import (
    SESSION_TTL_SECONDS,
    AgentService,
    HandoffNotFound,
    SessionConflict,
    SessionInvalid,
    _SessionCtx,
)
from tekijin.app import create_app
from tekijin.auth.principal import Principal
from tekijin.auth.tokens import create_access_token
from tekijin.config import get_settings
from tekijin.data.dashboard import dashboard_summary
from tekijin.data.db import get_sessionmaker
from tekijin.models.tables import Answer, Event, Feedback, Message, Question, Recommendation

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)
GOOD_Q = "現行のVPN機器で3拠点の拠点間接続について相談したいです"
# Vague, no extractable topic -> C1 confidence stays sub-threshold -> C2 still asks
# a clarification. Under the #113 safety valve a topic-only question routes straight
# through, so this is what now exercises the ask/followup interrupt path.
VAGUE_Q = "相談したいことがあります"


class _FakeRetriever:
    def __init__(self, *, people=(), **conf) -> None:
        self._payload = {
            "past_answers": list(conf.get("past_answers", [])),
            "documents": list(conf.get("documents", [])),
            "candidate_people": list(people),
            "answer_confidence": conf.get("answer_confidence", 0.0),
            "document_confidence": conf.get("document_confidence", 0.0),
            "people_confidence": conf.get("people_confidence", 0.0),
        }

    def search(self, query: str, *, query_vector=None) -> dict:
        return self._payload


class _FakeScorer:
    def __init__(self, recs: list[dict]) -> None:
        self._recs = recs

    def rank(self, topics, candidates, asker_id, now, *, top_k=3) -> dict:
        # Honour the decline loop: only recommend candidates still in the pool.
        recs = [r for r in self._recs if r["person_id"] in candidates]
        return {"recommendations": recs[:top_k]}


def _recs(*ids: int) -> list[dict]:
    return [
        {
            "person_id": i,
            "name": f"社員{i}",
            "dept": "営業部",
            "score": 0.9 - 0.01 * i,
            "confidence": "中",
            "reasons": [{"type": "self", "detail": "自己申告スキル"}],
        }
        for i in ids
    ]


# Every endpoint now requires auth (#241). These tests act as ADMIN by default so
# they can drive any asker_id/responder_id (admin may impersonate anyone) — the
# same freedom they had before auth. Non-admin restriction + login are covered by
# dedicated tests below and in test_auth_integration.py.
def _admin_headers() -> dict[str, str]:
    settings = get_settings()
    token = create_access_token(
        Principal(employee_id=None, name="管理者", dept=None, is_admin=True),
        secret=settings.auth_secret,
        ttl_hours=1,
    )
    return {"Authorization": f"Bearer {token}"}


def _user_headers(
    employee_id: int, *, name: str = "利用者", dept: str | None = None
) -> dict[str, str]:
    settings = get_settings()
    token = create_access_token(
        Principal(employee_id=employee_id, name=name, dept=dept, is_admin=False),
        secret=settings.auth_secret,
        ttl_hours=1,
    )
    return {"Authorization": f"Bearer {token}"}


def _app_client(service: AgentService) -> TestClient:
    """A TestClient over ``create_app`` pre-authenticated as admin (default)."""

    client = TestClient(create_app(agent_service=service))
    client.headers.update(_admin_headers())
    return client


def _client(
    engine,
    embedder,
    *,
    retriever=None,
    scorer=None,
    checkpointer=None,
    answerability_model=None,
    answerability_threshold=40,
    self_answer_model=None,
    knowledge_answer_min_similarity=None,
) -> TestClient:
    service = AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=checkpointer or MemorySaver(),
        embedder=embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=TemplateDraftModel(),
        answerability_model=answerability_model,
        answerability_threshold=answerability_threshold,
        self_answer_model=self_answer_model,
        knowledge_answer_min_similarity=knowledge_answer_min_similarity,
        retriever=retriever,
        scorer=scorer,
        now_factory=lambda: NOW,
    )
    return _app_client(service)


class _FixedSelfAnswer:
    """#291 self-answer composer stand-in for the SSE test."""

    def __init__(self, *, grounded: bool, answer: str = "", cites=()) -> None:
        self._grounded = grounded
        self._answer = answer
        self._cites = list(cites)

    def compose(self, question, evidence):
        from tekijin.agent.protocols import SelfAnswerResult

        if not self._grounded:
            return SelfAnswerResult(answer="", cited_source_ids=[], grounded=False)
        return SelfAnswerResult(answer=self._answer, cited_source_ids=self._cites, grounded=True)


class _FixedAnswerability:
    """#70 critic stand-in with a fixed confidence, for the SSE/persist tests."""

    def __init__(self, confidence: int) -> None:
        self._confidence = confidence

    def assess(self, question, candidate_evidence):
        from tekijin.agent.protocols import AnswerabilityResult

        return AnswerabilityResult(confidence=self._confidence, reason="判定理由")


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    event: str | None = None
    data: list[str] = []
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].strip())
        elif line == "" and event is not None:
            out.append((event, json.loads("".join(data)) if data else {}))
            event, data = None, []
    if event is not None:
        out.append((event, json.loads("".join(data)) if data else {}))
    return out


def _events(client: TestClient, session_id: str) -> list[tuple[str, dict]]:
    # sse-starlette keeps a module-global graceful-shutdown Event bound to the
    # loop of the first request; TestClient uses a fresh loop per request, so
    # reset it before each SSE call (a test-only artifact; real uvicorn has one
    # loop). It is lazily recreated on the current loop.
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    resp = client.get(f"/events/{session_id}")
    assert resp.status_code == 200
    return _parse_sse(resp.text)


@pytest.fixture(autouse=True)
def _cleanup_api_rows(engine):
    # The API COMMITS questions/recommendations (id prefix "api_"). Remove them
    # after each test so the committed seed / other tests stay isolated.
    from sqlalchemy import text

    yield
    session = get_sessionmaker(engine)()
    try:
        # messages FK-reference recommendations (no ON DELETE CASCADE), so chat
        # rows from #224 tests must go first.
        session.execute(
            text(
                r"DELETE FROM messages WHERE recommendation_id IN "
                r"(SELECT id FROM recommendations WHERE question_id LIKE 'api\_%' ESCAPE '\')"
            )
        )
        # feedback (#237) is runtime-only (seed writes none) and FKs questions, so
        # clear it first — before the questions it may reference are deleted.
        session.execute(text("DELETE FROM feedback"))
        session.execute(
            text(r"DELETE FROM recommendations WHERE question_id LIKE 'api\_%' ESCAPE '\'")
        )
        # events reference questions (FK) and are now written at runtime (#177), so
        # they must be removed before the questions they point at.
        session.execute(text(r"DELETE FROM events WHERE question_id LIKE 'api\_%' ESCAPE '\'"))
        # answers also FK questions (the #207 delete tests insert api_ answers).
        session.execute(text(r"DELETE FROM answers WHERE question_id LIKE 'api\_%' ESCAPE '\'"))
        session.execute(text(r"DELETE FROM questions WHERE id LIKE 'api\_%' ESCAPE '\'"))
        session.commit()
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# happy path: understood -> route -> recommend -> draft -> (resume) -> done
# --------------------------------------------------------------------------- #
def test_happy_path_ask_events_answer(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    ack = client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "s1"})
    assert ack.status_code == 200 and ack.json() == {"session_id": "s1", "status": "accepted"}

    evs = _events(client, "s1")
    names = [e for e, _ in evs]
    assert names == ["understood", "route", "recommend", "draft"]
    assert evs[0][1]["topics"] == ["ネットワーク・VPN"]
    assert evs[1][1]["route"] == "person"
    # person_id crosses the boundary in the external "E###" form (codex#7).
    assert [r["person_id"] for r in evs[2][1]["recommendations"]] == ["E001", "E002", "E003"]
    assert "社員1さん" in evs[3][1]["draft"]

    # Resume with the responder's acceptance -> done.
    ans = client.post("/answer", json={"session_id": "s1", "outcome": "accepted"})
    assert ans.status_code == 200 and ans.json()["status"] == "resumed"
    done = _events(client, "s1")
    assert len(done) == 1 and done[0][0] == "done"
    assert done[0][1]["status"] == "sent"
    assert "取り次ぎました" in done[0][1]["answer"]
    # #177: the done event carries the segment's processing latency (int ms).
    assert isinstance(done[0][1]["latency_ms"], int)


# --------------------------------------------------------------------------- #
# #177: per-stage run events (latency KPI source) + dashboard percentiles
# --------------------------------------------------------------------------- #
def _events_for(engine, question_id: str) -> list[Event]:
    check = get_sessionmaker(engine)()
    try:
        return check.query(Event).filter(Event.question_id == question_id).all()
    finally:
        check.close()


def test_run_records_stage_events(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "ev1"})
    _events(client, "ev1")  # run to the send interrupt

    qid = _latest_question(engine).id
    rows = _events_for(engine, qid)
    stages = {r.stage for r in rows}
    # Every compute stage of the person-route segment is recorded (not just the
    # ones that surface an SSE event), so the latency sum is real processing time.
    assert {"c1_intent", "c3_embed", "c4_retrieve", "c5_route", "c6_score", "c7_draft"} <= stages
    # No interrupt pseudo-node is recorded, and timing columns are populated.
    assert "__interrupt__" not in stages
    for r in rows:
        assert r.started_at is not None and r.ended_at is not None


def test_dashboard_exposes_processing_latency_from_events(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(engine, fake_embedder)
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "lat1"})
    _events(client, "lat1")

    body = client.get("/dashboard").json()
    lat = body["processing_latency"]
    assert lat["sample_size"] >= 1
    # p50/p95 are present and non-negative once at least one run is recorded.
    assert lat["p50_ms"] is not None and lat["p50_ms"] >= 0
    assert lat["p95_ms"] is not None and lat["p95_ms"] >= 0


def test_run_records_nonzero_monotonic_durations(seed_counts, engine, fake_embedder) -> None:
    # With an advancing clock (not the fixed test clock), each stage has a real,
    # positive, non-overlapping duration and the terminal reports a non-zero
    # latency_ms — exercising the actual prev-threading in _run (#177 review).
    import datetime as dt
    import itertools

    base = dt.datetime(2026, 1, 1, 0, 0, 0)
    ticks = itertools.count()

    svc = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer([]),  # empty -> reaches the no_candidate terminal (a done-ish end)
        now_factory=lambda: base + dt.timedelta(seconds=next(ticks)),
    )
    svc.start_question("dur1", 10, GOOD_Q)
    out = list(svc.stream_events("dur1"))

    # The terminal message carries a strictly-positive processing latency.
    msg = next(e for e in out if e.event == "message")
    assert json.loads(msg.data)["latency_ms"] > 0

    qid = _latest_question(engine).id
    rows = sorted(_events_for(engine, qid), key=lambda r: r.started_at)
    assert rows
    for r in rows:
        assert r.ended_at > r.started_at  # positive duration
    for a, b in zip(rows, rows[1:], strict=False):
        assert b.started_at >= a.ended_at  # non-overlapping, monotonic


# --------------------------------------------------------------------------- #
# clarification: followup -> reply resume -> continues
# --------------------------------------------------------------------------- #
def test_followup_then_reply_resumes(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    # Topic-only question -> missing slots -> C2 asks a followup.
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "s2"})
    first = _events(client, "s2")
    assert [e for e, _ in first] == ["understood", "followup"]
    assert "教えてください" in first[1][1]["question"]

    # Reply with the missing info -> resumes and runs through to the draft.
    client.post("/answer", json={"session_id": "s2", "reply": "現行はVPN機器で3拠点です"})
    cont = [e for e, _ in _events(client, "s2")]
    assert cont == ["understood", "route", "recommend", "draft"]


# --------------------------------------------------------------------------- #
# decline -> reroute to next candidate -> accept -> done
# --------------------------------------------------------------------------- #
def test_decline_reroutes_then_accept(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "s3"})
    first = _events(client, "s3")
    assert first[2][1]["recommendations"][0]["person_id"] == "E001"  # drafted for 1

    # Decline -> reroute -> re-scored excluding 1 -> next candidate 2.
    client.post("/answer", json={"session_id": "s3", "outcome": "declined"})
    second = _events(client, "s3")
    assert [e for e, _ in second] == ["recommend", "draft"]
    assert second[0][1]["recommendations"][0]["person_id"] == "E002"

    client.post("/answer", json={"session_id": "s3", "outcome": "accepted"})
    done = _events(client, "s3")
    assert done[0][0] == "done"

    # The decline never stamped resolved_at; the final accept does, once (#97).
    check = get_sessionmaker(engine)()
    try:
        q = check.query(Question).filter(Question.session_id == "s3").first()
        assert q is not None and q.resolved_at is not None
    finally:
        check.close()


# --------------------------------------------------------------------------- #
# dispatch guards: 409 on busy/paused, 422 on wrong resume kind
# --------------------------------------------------------------------------- #
def test_second_ask_while_queued_conflicts(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    assert (
        client.post(
            "/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "c1"}
        ).status_code
        == 200
    )
    # A run is already queued (not yet streamed) -> 409, does not overwrite.
    assert (
        client.post(
            "/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "c1"}
        ).status_code
        == 409
    )


def test_ask_while_paused_conflicts(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "c2"})
    _events(client, "c2")  # runs to the send interrupt (paused)
    # Session is awaiting a resume -> a new /ask is rejected.
    assert (
        client.post(
            "/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "c2"}
        ).status_code
        == 409
    )


def test_answer_wrong_kind_is_422(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    # Paused at send (expects an outcome) — sending a 'reply' is the wrong kind.
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "c3"})
    _events(client, "c3")
    assert client.post("/answer", json={"session_id": "c3", "reply": "x"}).status_code == 422

    # Paused at ask (expects a reply) — sending an 'outcome' is the wrong kind.
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "c4"})
    _events(client, "c4")
    assert (
        client.post("/answer", json={"session_id": "c4", "outcome": "accepted"}).status_code == 422
    )


def test_answer_without_interrupt_conflicts(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    # Never asked / not paused -> 409.
    assert (
        client.post("/answer", json={"session_id": "never", "outcome": "accepted"}).status_code
        == 409
    )


# --------------------------------------------------------------------------- #
# reconnect: /events again on a paused session re-emits the pending interrupt
# --------------------------------------------------------------------------- #
def test_reconnect_resends_pending_interrupt(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    # send interrupt -> reconnect re-sends the candidates AND the draft, so a
    # client that reconnects (or one that reads after another consumer drained the
    # live segment) can fully reconstruct the hand-off (#38 re-review).
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "r1"})
    _events(client, "r1")
    again = _events(client, "r1")  # no /answer: reconnect
    assert [e for e, _ in again] == ["recommend", "draft"]
    assert again[0][1]["recommendations"][0]["person_id"] == "E001"

    # ask interrupt -> reconnect re-sends the followup.
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "r2"})
    _events(client, "r2")
    again2 = _events(client, "r2")
    assert [e for e, _ in again2] == ["followup"]


# --------------------------------------------------------------------------- #
# persistence: /ask writes a Question, C6 a Recommendation, /answer the outcome
# --------------------------------------------------------------------------- #
def test_flow_persists_question_and_recommendation(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2]),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 7, "question": GOOD_Q, "session_id": "p1"})
    _events(client, "p1")
    client.post("/answer", json={"session_id": "p1", "outcome": "accepted"})
    _events(client, "p1")

    check = get_sessionmaker(engine)()
    try:
        q = (
            check.query(Question)
            .filter(Question.asker_id == 7, Question.body == GOOD_Q)
            .order_by(Question.created_at.desc())
            .first()
        )
        assert q is not None and q.id.startswith("api_")  # uuid-based, collision-free
        assert q.topics == ["ネットワーク・VPN"]  # C1 topics backfilled
        assert q.route == "person"  # C5 route persisted (dashboard 自己解決率 source)
        recs = (
            check.query(Recommendation)
            .filter(Recommendation.question_id == q.id)
            .order_by(Recommendation.rank)
            .all()
        )
        # Every shown recommendation (rank 1..2) is persisted (codex#4).
        assert [r.rank for r in recs] == [1, 2]
        assert [r.employee_id for r in recs] == [1, 2]
        # Only the primary (rank 1, handed off) carries the outcome; the rest are
        # "shown" rows with outcome=NULL.
        assert recs[0].outcome == "accepted"
        assert recs[1].outcome is None
        # An accepted hand-off stamps the runtime resolution time (#97).
        assert q.resolved_at is not None
    finally:
        check.close()


def test_document_route_stamps_resolved_at(seed_counts, engine, fake_embedder) -> None:
    # A self-resolving document route records resolved_at even though no responder
    # ever accepts — so it counts toward the dashboard's avg resolution time (#97).
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(
            documents=[{"doc_id": "doc_001", "score": 0.05}],
            document_confidence=0.8,  # strongly on-topic document
            people_confidence=0.2,  # weak person signal -> document wins
            people=[1, 2, 3],
        ),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 8, "question": GOOD_Q, "session_id": "docres"})
    events = _events(client, "docres")
    names = [e for e, _ in events]
    assert "message" in names  # terminal document message, no hand-off
    # #279/#281: C6 runs on the document route to add an inline person fallback to
    # the answer, but it is NOT a live hand-off — so no `recommend` event fires and
    # NO Recommendation rows are persisted (they could never resolve on a terminal
    # document route and would inflate the pending-handoff KPI / pollute inboxes).
    assert "recommend" not in names
    message = next(data for e, data in events if e == "message")
    assert "社員1さん" in message.get("message", "")  # the inline person fallback

    check = get_sessionmaker(engine)()
    try:
        q = check.query(Question).filter(Question.session_id == "docres").first()
        assert q is not None and q.route == "document"
        assert q.resolved_at is not None
        # No phantom recommendation rows for the self-resolving document route.
        assert check.query(Recommendation).filter(Recommendation.question_id == q.id).count() == 0
    finally:
        check.close()


# --------------------------------------------------------------------------- #
# self-answer (#291): a grounded answer terminates with cited source links
# --------------------------------------------------------------------------- #
def test_self_answer_message_carries_citations(seed_counts, engine, fake_embedder) -> None:
    # A strong document route + a grounded composer -> the run answers directly
    # (status=self_answered) with the cited sources, and does NOT hand off (no
    # recommend/draft events).
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(
            documents=[{"doc_id": "doc_001", "score": 0.05}],
            document_confidence=0.8,
            people_confidence=0.2,
            people=[1, 2],
        ),
        scorer=_FakeScorer(_recs(1, 2)),
        self_answer_model=_FixedSelfAnswer(
            grounded=True, answer="保守時間内に更新します。", cites=["doc_001"]
        ),
    )
    client.post("/ask", json={"asker_id": 8, "question": GOOD_Q, "session_id": "sa"})
    events = _events(client, "sa")
    names = [e for e, _ in events]
    assert "recommend" not in names and "draft" not in names  # not a hand-off
    message = next(data for e, data in events if e == "message")
    assert message["status"] == "self_answered"
    assert message["message"] == "保守時間内に更新します。"
    assert message["citations"] == [{"source_id": "doc_001", "kind": "document"}]


def test_knowledge_answer_persists_route_and_self_resolution(
    seed_counts, engine, fake_embedder
) -> None:
    # #357 slice 4c: a grounded knowledge answer terminates before C5, so the run
    # must still (a) emit a self_answered message and (b) persist a synthetic
    # "knowledge" route + mark the question self-resolved (dashboards segment by route).
    from tekijin.data.knowledge import (
        get_knowledge_unit_by_source,
        set_review_status,
        upsert_knowledge_unit,
    )
    from tekijin.knowledge.index import embed_knowledge_units

    setup = get_sessionmaker(engine)()
    try:
        upsert_knowledge_unit(
            setup,
            kind="case",
            problem=GOOD_Q,
            action="SFA/CRM を提案",
            result="受注",
            topics=["CRM・営業支援"],
            source_type="daily_report",
            source_id="kb_api_1",
            confidence=0.9,
        )
        setup.flush()
        dto = get_knowledge_unit_by_source(setup, "daily_report", "kb_api_1")
        set_review_status(setup, dto.id, "approved")
        setup.flush()
        embed_knowledge_units(setup, fake_embedder)
        setup.commit()
    finally:
        setup.close()

    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2]),
        knowledge_answer_min_similarity=0.1,
    )
    client.post("/ask", json={"asker_id": 8, "question": GOOD_Q, "session_id": "skb"})
    events = _events(client, "skb")
    assert next(d for e, d in events if e == "message")["status"] == "self_answered"

    check = get_sessionmaker(engine)()
    try:
        q = check.query(Question).filter(Question.session_id == "skb").first()
        assert q is not None and q.route == "knowledge"  # synthetic route persisted
        assert q.resolved_at is not None and q.resolution_kind == "self"
    finally:
        check.close()


def test_self_answer_on_prior_answer_route_marks_self_resolved(
    seed_counts, engine, fake_embedder
) -> None:
    # #291 review (HIGH): a grounded self-answer on the prior_answer route (which is
    # NOT stamped at c5 like document) must still count as self-resolved — else the
    # KPI this feature exists to move undercounts. resolution_kind="self" + resolved_at.
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(
            people=[1],
            past_answers=[{"qa_id": "a", "score": 0.05, "responder_id": 1}],
            answer_confidence=0.9,  # near-duplicate past QA -> prior_answer route
        ),
        self_answer_model=_FixedSelfAnswer(grounded=True, answer="過去回答より。", cites=[]),
    )
    client.post("/ask", json={"asker_id": 8, "question": GOOD_Q, "session_id": "sapa"})
    events = _events(client, "sapa")
    assert next(d for e, d in events if e == "message")["status"] == "self_answered"

    check = get_sessionmaker(engine)()
    try:
        q = check.query(Question).filter(Question.session_id == "sapa").first()
        assert q is not None and q.route == "prior_answer"
        assert q.resolved_at is not None  # stamped despite not being the document route
        assert q.resolution_kind == "self"  # counts toward the self-resolution rate
    finally:
        check.close()


# --------------------------------------------------------------------------- #
# answerability critic (#70): SSE + persistence gated on the critic's verdict
# --------------------------------------------------------------------------- #
def test_answerability_accept_surfaces_recommend_and_persists(
    seed_counts, engine, fake_embedder
) -> None:
    # A confident critic behaves exactly like the pre-#70 happy path: recommend
    # event fires and the shown rows are persisted for the outcome record.
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
        answerability_model=_FixedAnswerability(confidence=85),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "ans_ok"})
    names = [e for e, _ in _events(client, "ans_ok")]
    assert names == ["understood", "route", "recommend", "draft"]  # unchanged flow

    check = get_sessionmaker(engine)()
    try:
        q = check.query(Question).filter(Question.session_id == "ans_ok").first()
        assert q is not None
        assert check.query(Recommendation).filter(Recommendation.question_id == q.id).count() == 3
    finally:
        check.close()


def test_answerability_reject_suppresses_recommend_and_persists_nothing(
    seed_counts, engine, fake_embedder
) -> None:
    # A rejecting critic: NO recommend event (the held one is dropped), NO draft,
    # a `no_expert` terminal message, and — critically — NO Recommendation rows
    # (a rejected set must never become a phantom pending row / inbox dead-link,
    # the same integrity guard as the document route, #281).
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
        answerability_model=_FixedAnswerability(confidence=15),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "ans_ng"})
    events = _events(client, "ans_ng")
    names = [e for e, _ in events]
    assert "recommend" not in names and "draft" not in names
    assert names[-1] == "message"
    message = next(data for e, data in events if e == "message")
    assert message["status"] == "no_expert"
    assert "社内の実績が見つかりません" in message["message"]

    check = get_sessionmaker(engine)()
    try:
        q = check.query(Question).filter(Question.session_id == "ans_ng").first()
        assert q is not None and q.route == "person"
        # No phantom recommendation rows for the rejected hand-off.
        assert check.query(Recommendation).filter(Recommendation.question_id == q.id).count() == 0
    finally:
        check.close()


# --------------------------------------------------------------------------- #
# validation / errors
# --------------------------------------------------------------------------- #
def test_ask_validation_422(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert (
        client.post("/ask", json={"asker_id": 1, "question": "", "session_id": "s"}).status_code
        == 422
    )
    assert (
        client.post("/ask", json={"question": "q", "session_id": "s"}).status_code == 422
    )  # missing asker_id


def test_answer_invalid_outcome_422(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert client.post("/answer", json={"session_id": "s", "outcome": "maybe"}).status_code == 422
    assert client.post("/answer", json={"session_id": "s"}).status_code == 422  # neither field


def test_events_unknown_session_404(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert client.get("/events/nonexistent").status_code == 404


def test_cors_headers_present(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_lifespan_startup_and_shutdown(seed_counts, engine, fake_embedder) -> None:
    # ``with TestClient`` triggers the FastAPI lifespan: startup log, then shutdown
    # calls service.close() (engine.dispose is safe — the engine re-pools on demand).
    service = AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=MemorySaver(),
        embedder=fake_embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=TemplateDraftModel(),
        retriever=_FakeRetriever(),
        scorer=_FakeScorer([]),
        now_factory=lambda: NOW,
    )
    with _app_client(service) as client:
        assert client.get("/health").status_code == 200


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #
def test_dashboard_route_shape_and_seed_values(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    body = client.get("/dashboard").json()
    assert body["total_employees"] == 40
    assert body["total_questions"] == 150
    assert body["total_answers"] == 150
    assert body["recommendation_count"] == 0  # none persisted yet
    assert body["answers_per_responder"]  # load distribution from seed
    assert all("answer_count" in r for r in body["answers_per_responder"])
    assert body["topic_distribution"]  # topic mix from seed questions
    # Aggregate-only: no per-record listing (codex#5, product-spec §241-251).
    assert "recent_recommendations" not in body
    assert body["recommendation_outcomes"] == {"accepted": 0, "declined": 0, "pending": 0}
    assert body["acceptance_rate"] == 0.0
    # product-spec 画面5 metrics are present.
    assert body["self_resolution_rate"] == 0.0  # no routed questions in the seed
    assert body["avg_resolution_hours"] is not None and body["avg_resolution_hours"] > 0
    assert 0.0 <= body["top_responder_share"] <= 1.0
    assert body["latest_eval"] is None  # no eval snapshot stored yet


def test_employees_route_lists_directory_for_switcher(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    body = client.get("/employees").json()
    people = body["employees"]
    assert len(people) == 40  # the seeded roster
    # ids are the external "E###" form (round-trips as asker_id / responder id),
    # ordered by employee id ascending.
    ids = [p["id"] for p in people]
    assert ids[0] == "E001"
    assert ids == sorted(ids)
    assert all(p["name"] for p in people)  # every row has a display name
    assert all(set(p) == {"id", "name", "dept"} for p in people)


def test_inbox_lists_pending_handoff_then_clears_after_answer(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "inbox-s1"})
    _events(client, "inbox-s1")  # run to the send interrupt: persists recs + session_id

    # The primary (rank 1) responder E001 sees the pending handoff, deep-linkable
    # by its session id; the question and asker are previewed.
    body = client.get("/inbox", params={"responder_id": "E001"}).json()
    item = next(i for i in body["items"] if i["session_id"] == "inbox-s1")
    assert item["asker"]["id"] == "E010"
    assert item["question"] == GOOD_Q
    assert item["topics"]  # C1 topics persisted onto the question
    # Never-chosen defaults to "chat"; the responder must see this BEFORE
    # accepting, since "direct" means no chat thread is ever opened (#245).
    assert item["consult_method"] == "chat"

    # A non-primary candidate (E002) was not handed off -> nothing pending.
    assert client.get("/inbox", params={"responder_id": "E002"}).json()["items"] == []

    # Choosing 直接相談 at send time surfaces on the inbox item (#245).
    client.post(
        "/handoff/draft",
        json={"session_id": "inbox-s1", "draft": "直接うかがいます", "consult_method": "direct"},
    )
    direct = client.get("/inbox", params={"responder_id": "E001"}).json()
    picked = next(i for i in direct["items"] if i["session_id"] == "inbox-s1")
    assert picked["consult_method"] == "direct"

    # Once the responder accepts, the handoff clears from the inbox.
    client.post("/answer", json={"session_id": "inbox-s1", "outcome": "accepted"})
    _events(client, "inbox-s1")  # drain so the outcome is recorded
    after = client.get("/inbox", params={"responder_id": "E001"}).json()
    assert all(i["session_id"] != "inbox-s1" for i in after["items"])


def test_inbox_rejects_a_bad_responder_id(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert client.get("/inbox", params={"responder_id": "not-an-id"}).status_code == 422


def test_questions_route_reflects_asker_history_and_resolution(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "rh-s1"})
    _events(client, "rh-s1")  # reach the send interrupt: question persisted, not yet accepted

    body = client.get("/questions", params={"asker_id": "E010"}).json()
    mine = next(i for i in body["items"] if i["title"] == GOOD_Q)
    assert mine["resolved"] is False and mine["responder_name"] is None

    # After acceptance the same question shows resolved with the responder's name.
    client.post("/answer", json={"session_id": "rh-s1", "outcome": "accepted"})
    _events(client, "rh-s1")
    after = client.get("/questions", params={"asker_id": "E010"}).json()
    mine_after = next(i for i in after["items"] if i["title"] == GOOD_Q)
    assert mine_after["resolved"] is True and mine_after["responder_name"]


def test_questions_route_rejects_a_bad_asker_id(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert client.get("/questions", params={"asker_id": "nope"}).status_code == 422


def test_document_route_returns_seeded_document(seed_counts, engine, fake_embedder) -> None:
    # The document viewer (#143) fetches a cited doc by id; doc_001 is seeded.
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.get("/documents/doc_001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "doc_001"
    assert body["title"] and body["body"]  # full content present


def test_document_route_unknown_id_is_404(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert client.get("/documents/doc_nope").status_code == 404


# --------------------------------------------------------------------------- #
# GET/POST /messages : chat threads on accepted recommendations (#224)
# --------------------------------------------------------------------------- #
def test_message_thread_appears_for_both_parties_after_acceptance(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "msg-s1"})
    _events(client, "msg-s1")

    # Before acceptance, neither party has a thread yet.
    assert client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"] == []
    assert client.get("/messages/threads", params={"employee_id": "E001"}).json()["items"] == []

    client.post("/answer", json={"session_id": "msg-s1", "outcome": "accepted"})
    _events(client, "msg-s1")

    asker_threads = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"]
    responder_threads = client.get("/messages/threads", params={"employee_id": "E001"}).json()[
        "items"
    ]
    assert len(asker_threads) == 1
    assert len(responder_threads) == 1
    thread_id = asker_threads[0]["thread_id"]
    assert responder_threads[0]["thread_id"] == thread_id
    assert asker_threads[0]["counterpart"]["id"] == "E001"
    assert responder_threads[0]["counterpart"]["id"] == "E010"
    assert asker_threads[0]["question_title"] == GOOD_Q


def test_accepted_thread_is_seeded_with_the_asker_draft_as_first_message(
    seed_counts, engine, fake_embedder
) -> None:
    # The asker's own request text (the hand-off draft) must be visible in the
    # chat, not just implied by the question preview — so the responder opens
    # the thread already seeing what they were asked.
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "msg-seed1"})
    _events(client, "msg-seed1")
    draft = client.get("/handoff/msg-seed1").json()["draft"]
    assert draft

    client.post("/answer", json={"session_id": "msg-seed1", "outcome": "accepted"})
    _events(client, "msg-seed1")
    thread_id = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"][0][
        "thread_id"
    ]

    detail = client.get(f"/messages/threads/{thread_id}", params={"employee_id": "E001"}).json()
    assert detail["messages"][0]["body"] == draft
    assert detail["messages"][0]["sender_id"] == "E010"

    listing = client.get("/messages/threads", params={"employee_id": "E001"}).json()["items"][0]
    assert listing["last_message"] == draft


def test_direct_consultation_gets_no_seeded_message(seed_counts, engine, fake_embedder) -> None:
    # No chat thread exists at all for "direct", so there is nothing to seed —
    # covered here as a guard against the seeding write itself erroring out.
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "msg-seed2"})
    _events(client, "msg-seed2")
    client.post(
        "/handoff/draft",
        json={"session_id": "msg-seed2", "draft": "本文", "consult_method": "direct"},
    )
    answered = client.post("/answer", json={"session_id": "msg-seed2", "outcome": "accepted"})
    assert answered.status_code == 200
    _events(client, "msg-seed2")

    threads = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"]
    assert all(t["question_title"] != GOOD_Q for t in threads)


# --------------------------------------------------------------------------- #
# consultation method: 直接相談 / チャットで相談
# --------------------------------------------------------------------------- #
def test_consult_method_defaults_to_chat_when_never_set(seed_counts, engine, fake_embedder) -> None:
    # Backward compatibility: an asker who never calls POST /handoff/draft (or a
    # client that predates this field) behaves exactly as before — "chat" everywhere.
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "cm-default"})
    _events(client, "cm-default")

    assert client.get("/handoff/cm-default").json()["consult_method"] == "chat"

    client.post("/answer", json={"session_id": "cm-default", "outcome": "accepted"})
    _events(client, "cm-default")
    threads = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"]
    assert any(t["question_title"] == GOOD_Q for t in threads)


def test_consult_method_direct_is_visible_before_acceptance(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "cm-direct1"})
    _events(client, "cm-direct1")

    saved = client.post(
        "/handoff/draft",
        json={"session_id": "cm-direct1", "draft": "本文", "consult_method": "direct"},
    )
    assert saved.status_code == 200

    assert client.get("/handoff/cm-direct1").json()["consult_method"] == "direct"


def test_consult_method_direct_never_gets_a_chat_thread(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "cm-direct2"})
    _events(client, "cm-direct2")
    client.post(
        "/handoff/draft",
        json={"session_id": "cm-direct2", "draft": "本文", "consult_method": "direct"},
    )
    # The accepted recommendation id doubles as the thread id (#224's scheme);
    # capture it before accepting — GET /handoff 404s once the outcome lands.
    thread_id = client.get("/handoff/cm-direct2").json()["recommendation_id"]
    assert thread_id is not None

    client.post("/answer", json={"session_id": "cm-direct2", "outcome": "accepted"})
    _events(client, "cm-direct2")

    asker_threads = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"]
    responder_threads = client.get("/messages/threads", params={"employee_id": "E001"}).json()[
        "items"
    ]
    assert all(t["question_title"] != GOOD_Q for t in asker_threads)
    assert all(t["question_title"] != GOOD_Q for t in responder_threads)

    # Even knowing the id, a "direct" consultation's thread is 404 for both parties.
    assert (
        client.get(f"/messages/threads/{thread_id}", params={"employee_id": "E010"}).status_code
        == 404
    )
    assert (
        client.post(
            "/messages", json={"thread_id": thread_id, "sender_id": "E010", "body": "hi"}
        ).status_code
        == 404
    )


def test_consult_method_survives_decline_and_reroute(seed_counts, engine, fake_embedder) -> None:
    # consult_method lives on the Question (not the Recommendation), so it must
    # carry over to the NEW rank-1 recommendation a decline+reroute creates.
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "cm-reroute"})
    _events(client, "cm-reroute")
    client.post(
        "/handoff/draft",
        json={"session_id": "cm-reroute", "draft": "本文", "consult_method": "direct"},
    )

    client.post("/answer", json={"session_id": "cm-reroute", "outcome": "declined"})
    _events(client, "cm-reroute")
    assert client.get("/handoff/cm-reroute").json()["consult_method"] == "direct"


def test_send_and_list_messages_round_trip(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "msg-s2"})
    _events(client, "msg-s2")
    client.post("/answer", json={"session_id": "msg-s2", "outcome": "accepted"})
    _events(client, "msg-s2")
    thread_id = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"][0][
        "thread_id"
    ]

    send1 = client.post(
        "/messages",
        json={"thread_id": thread_id, "sender_id": "E010", "body": "よろしくお願いします"},
    )
    assert send1.status_code == 200
    assert send1.json()["sender_id"] == "E010"
    send2 = client.post(
        "/messages", json={"thread_id": thread_id, "sender_id": "E001", "body": "承知しました"}
    )
    assert send2.status_code == 200

    for employee_id in ("E010", "E001"):
        detail = client.get(
            f"/messages/threads/{thread_id}", params={"employee_id": employee_id}
        ).json()
        # The first message is auto-seeded from the asker's hand-off draft at
        # acceptance time, so the responder opens the thread already seeing what
        # they were asked, not an empty conversation.
        bodies = [m["body"] for m in detail["messages"]]
        assert bodies[1:] == ["よろしくお願いします", "承知しました"]
        assert bodies[0]  # non-empty draft text
        assert [m["sender_id"] for m in detail["messages"]] == ["E010", "E010", "E001"]

    listing = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"][0]
    assert listing["last_message"] == "承知しました"
    assert listing["last_message_at"] is not None


def test_message_thread_rejects_non_party(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "msg-s3"})
    _events(client, "msg-s3")
    client.post("/answer", json={"session_id": "msg-s3", "outcome": "accepted"})
    _events(client, "msg-s3")
    thread_id = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"][0][
        "thread_id"
    ]

    assert (
        client.get(f"/messages/threads/{thread_id}", params={"employee_id": "E004"}).status_code
        == 404
    )
    assert (
        client.post(
            "/messages", json={"thread_id": thread_id, "sender_id": "E004", "body": "hi"}
        ).status_code
        == 404
    )


def test_message_thread_404_before_acceptance_and_for_declined(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "msg-s4"})
    _events(client, "msg-s4")
    pending_rid = client.get("/handoff/msg-s4").json()["recommendation_id"]

    # Not yet accepted -> 404 for both parties.
    assert (
        client.get(f"/messages/threads/{pending_rid}", params={"employee_id": "E010"}).status_code
        == 404
    )

    # Decline -> reroute. The old (now declined) recommendation id stays 404
    # forever, even after a different recommendation on the same question is
    # accepted (#94-style reroute).
    client.post("/answer", json={"session_id": "msg-s4", "outcome": "declined"})
    _events(client, "msg-s4")
    new_rid = client.get("/handoff/msg-s4").json()["recommendation_id"]
    assert new_rid != pending_rid
    client.post("/answer", json={"session_id": "msg-s4", "outcome": "accepted"})
    _events(client, "msg-s4")

    assert (
        client.get(f"/messages/threads/{pending_rid}", params={"employee_id": "E010"}).status_code
        == 404
    )
    assert (
        client.get(f"/messages/threads/{new_rid}", params={"employee_id": "E010"}).status_code
        == 200
    )


def test_send_message_rejects_blank_body(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "msg-s5"})
    _events(client, "msg-s5")
    client.post("/answer", json={"session_id": "msg-s5", "outcome": "accepted"})
    _events(client, "msg-s5")
    thread_id = client.get("/messages/threads", params={"employee_id": "E010"}).json()["items"][0][
        "thread_id"
    ]

    resp = client.post(
        "/messages", json={"thread_id": thread_id, "sender_id": "E010", "body": "   "}
    )
    assert resp.status_code == 422


def test_message_threads_route_rejects_bad_employee_id(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert client.get("/messages/threads", params={"employee_id": "not-an-id"}).status_code == 422


def test_dashboard_self_resolution_and_latest_eval(seed_counts, session) -> None:
    from sqlalchemy import update

    from tekijin.data.writes import insert_eval_run
    from tekijin.models.tables import Question

    # Route three seeded questions. Only ``document`` counts as self-resolved:
    # ``prior_answer`` still hands off to the pinned responder in the current graph.
    routes = {"q_0001": "document", "q_0002": "prior_answer", "q_0003": "person"}
    for qid, route in routes.items():
        session.execute(update(Question).where(Question.id == qid).values(route=route))
    session.flush()

    summary = dashboard_summary(session)
    assert summary["self_resolution_rate"] == pytest.approx(1 / 3)  # only the document route

    # No eval snapshot -> None; after storing one, the latest is returned.
    assert summary["latest_eval"] is None
    insert_eval_run(
        session,
        {"top1_accuracy": 0.7, "recall_at_3": 0.6, "mrr": 0.72, "route_accuracy": 0.7},
    )
    session.flush()
    assert dashboard_summary(session)["latest_eval"]["top1_accuracy"] == pytest.approx(0.7)


def test_top_responder_share_zero_when_no_answers() -> None:
    from tekijin.data.dashboard import _top_responder_share

    assert _top_responder_share([], 0) == 0.0  # no answers -> no concentration
    assert _top_responder_share([{"answer_count": 4}], 10) == pytest.approx(0.4)


def test_dashboard_summary_aggregates_outcomes(seed_counts, session) -> None:
    # Direct data-layer test: flushed rows are visible within the same session.
    # 2 accepted, 1 declined, 1 pending -> acceptance_rate = 2/3.
    for eid, outcome in [(3, "accepted"), (4, "accepted"), (5, "declined"), (6, None)]:
        session.add(
            Recommendation(
                question_id="q_0001", employee_id=eid, rank=1, score=0.8, outcome=outcome
            )
        )
    session.flush()
    summary = dashboard_summary(session)
    assert summary["recommendation_count"] >= 4
    assert summary["recommendation_outcomes"] == {"accepted": 2, "declined": 1, "pending": 1}
    assert summary["acceptance_rate"] == 2 / 3
    assert "recent_recommendations" not in summary  # aggregate-only


# --------------------------------------------------------------------------- #
# GET /handoff : responder-facing payload for a session paused at ``send`` (#38)
# --------------------------------------------------------------------------- #
def test_handoff_returns_responder_payload(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "h1"})
    _events(client, "h1")  # run to the send interrupt (paused, draft ready)

    resp = client.get("/handoff/h1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "h1"
    assert body["question"] == GOOD_Q
    # asker enriched from the DB and exposed in the external "E###" form.
    assert body["asker"]["id"] == "E010"
    assert body["asker"]["name"]  # employee 10 exists in the seed
    # responder = the primary (handed-off) candidate, with its selection reasons.
    assert body["responder"]["person_id"] == "E001"
    assert body["responder"]["confidence"] == "中"
    assert body["responder"]["reasons"][0]["type"] == "self"
    # slots surfaced from the understood state; draft addressed to the responder.
    assert body["topics"] == ["ネットワーク・VPN"]
    assert "社員1さん" in body["draft"]
    # reuse aggregates are present and integral (exact totals covered in the unit).
    assert isinstance(body["reuse_count"], int)
    assert isinstance(body["helpful_answer_count"], int)
    # generation token for the stale-outcome guard (#94).
    assert isinstance(body["recommendation_id"], int)


def test_stale_outcome_recommendation_id_is_rejected(seed_counts, engine, fake_embedder) -> None:
    # After a decline→reroute the primary moves on; an outcome carrying the OLD
    # recommendation id (a competing tab / late submit) must 409, never bind the
    # accept to the new candidate. The current id still succeeds (#94).
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "stale1"})
    _events(client, "stale1")
    old_rid = client.get("/handoff/stale1").json()["recommendation_id"]
    assert old_rid is not None

    client.post("/answer", json={"session_id": "stale1", "outcome": "declined"})
    _events(client, "stale1")
    new_rid = client.get("/handoff/stale1").json()["recommendation_id"]
    assert new_rid is not None and new_rid != old_rid

    stale = client.post(
        "/answer",
        json={"session_id": "stale1", "outcome": "accepted", "recommendation_id": old_rid},
    )
    assert stale.status_code == 409

    ok = client.post(
        "/answer",
        json={"session_id": "stale1", "outcome": "accepted", "recommendation_id": new_rid},
    )
    assert ok.status_code == 200


def test_handoff_conflicts_when_awaiting_clarification(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    # Topic-only question -> paused at ``ask`` (a followup is owed to the asker).
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "h2"})
    _events(client, "h2")
    assert client.get("/handoff/h2").status_code == 409  # not a responder handoff


def test_handoff_unknown_session_404(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert client.get("/handoff/nonexistent").status_code == 404


def test_handoff_finished_session_404(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "h4"})
    _events(client, "h4")
    client.post("/answer", json={"session_id": "h4", "outcome": "accepted"})
    _events(client, "h4")  # run to completion (done) — no longer awaiting an outcome
    assert client.get("/handoff/h4").status_code == 404


def test_handoff_gone_once_outcome_queued(seed_counts, engine, fake_embedder) -> None:
    # After an outcome is submitted (queued in the registry) but before an /events
    # reader consumes it, the durable snapshot still shows next==("send",). The
    # handoff must report itself as no-longer-offerable so a reload does not
    # re-render the form and invite a duplicate submission.
    svc = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    svc.start_question("hq", 10, GOOD_Q)
    list(svc.stream_events("hq"))  # pause at send
    svc.submit_resume("hq", outcome="accepted")  # queues the resume (not yet drained)
    with pytest.raises(HandoffNotFound):
        svc.get_handoff("hq")


# --------------------------------------------------------------------------- #
# POST /handoff/draft : persist the asker's edited hand-off draft (#174)
# --------------------------------------------------------------------------- #
def test_handoff_draft_persists_edited_text_for_the_responder(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hd1"})
    _events(client, "hd1")  # pause at send (draft ready)
    before = client.get("/handoff/hd1").json()
    rec_id_before = before["recommendation_id"]

    edited = "社員1さん、お忙しいところ恐れ入ります。VPNの移行の件でご相談です。"
    saved = client.post("/handoff/draft", json={"session_id": "hd1", "draft": edited})
    assert saved.status_code == 200
    assert saved.json()["status"] == "draft_saved"

    # The responder now reads the edited draft — and the outcome token is untouched.
    after = client.get("/handoff/hd1").json()
    assert after["draft"] == edited
    assert after["recommendation_id"] == rec_id_before
    # The accept/decline path still works after a draft edit.
    assert (
        client.post("/answer", json={"session_id": "hd1", "outcome": "accepted"}).status_code == 200
    )


def test_handoff_draft_rejects_blank(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hd2"})
    _events(client, "hd2")
    assert (
        client.post("/handoff/draft", json={"session_id": "hd2", "draft": "   "}).status_code == 422
    )


def test_handoff_draft_404_when_no_pending_handoff(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.post("/handoff/draft", json={"session_id": "nope", "draft": "本文"})
    assert resp.status_code == 404


def test_handoff_draft_409_when_awaiting_clarification(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "hd3"})
    _events(client, "hd3")  # paused at ``ask`` (a clarification is owed to the asker)
    resp = client.post("/handoff/draft", json={"session_id": "hd3", "draft": "本文"})
    assert resp.status_code == 409


def test_handoff_draft_404_after_answered(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hd4"})
    _events(client, "hd4")
    client.post("/answer", json={"session_id": "hd4", "outcome": "accepted"})
    _events(client, "hd4")  # completed
    resp = client.post("/handoff/draft", json={"session_id": "hd4", "draft": "本文"})
    assert resp.status_code == 404


def test_save_handoff_draft_rejects_blank_at_the_service(
    seed_counts, engine, fake_embedder
) -> None:
    # Defense in depth: a direct caller (bypassing the request schema) still cannot
    # persist a blank draft.
    svc = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    svc.start_question("hd5", 10, GOOD_Q)
    list(svc.stream_events("hd5"))  # pause at send
    with pytest.raises(SessionInvalid):
        svc.save_handoff_draft("hd5", "   ", "chat")


def test_save_handoff_draft_gone_once_outcome_queued(seed_counts, engine, fake_embedder) -> None:
    # Mirrors get_handoff: an outcome queued (not yet drained) leaves next==("send",)
    # but the hand-off is no longer editable.
    svc = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    svc.start_question("hd6", 10, GOOD_Q)
    list(svc.stream_events("hd6"))  # pause at send
    svc.submit_resume("hd6", outcome="accepted")  # queue the resume (not drained)
    with pytest.raises(HandoffNotFound):
        svc.save_handoff_draft("hd6", "編集後の本文", "chat")


def test_handoff_draft_unexpected_error_is_generic_500(seed_counts, engine, fake_embedder) -> None:
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))

    def _boom(*_a, **_k):
        raise RuntimeError("secret internal detail at 10.0.0.1")

    service.save_handoff_draft = _boom  # type: ignore[method-assign]
    client = _app_client(service)
    resp = client.post("/handoff/draft", json={"session_id": "anything", "draft": "本文"})
    assert resp.status_code == 500
    assert "内部エラー" in resp.text
    assert "secret internal" not in resp.text  # detail logged, never leaked


# --------------------------------------------------------------------------- #
# POST /handoff/select : asker reselects a different shown candidate (#200/#A1)
# --------------------------------------------------------------------------- #
def test_handoff_select_reorders_and_redrafts(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hs1"})
    _events(client, "hs1")  # pause at send, drafted for E001
    before = client.get("/handoff/hs1").json()
    assert before["responder"]["person_id"] == "E001"

    resp = client.post("/handoff/select", json={"session_id": "hs1", "person_id": "E003"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["responder"]["person_id"] == "E003"
    assert "社員3" in body["draft"]
    assert body["recommendation_id"] != before["recommendation_id"]

    after = client.get("/handoff/hs1").json()
    assert after["responder"]["person_id"] == "E003"
    assert after["draft"] == body["draft"]
    assert after["recommendation_id"] == body["recommendation_id"]

    # /inbox reflects the DB rank sync: E003 now has the pending handoff, E001 no
    # longer does.
    assert client.get("/inbox", params={"responder_id": "E003"}).json()["items"]
    assert client.get("/inbox", params={"responder_id": "E001"}).json()["items"] == []

    assert (
        client.post("/answer", json={"session_id": "hs1", "outcome": "accepted"}).status_code == 200
    )


def test_handoff_select_rejects_unknown_person_id(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hs2"})
    _events(client, "hs2")
    resp = client.post("/handoff/select", json={"session_id": "hs2", "person_id": "E099"})
    assert resp.status_code == 422


def test_handoff_select_404_when_no_pending_handoff(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.post("/handoff/select", json={"session_id": "nope", "person_id": "E001"})
    assert resp.status_code == 404


def test_handoff_select_409_when_awaiting_clarification(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "hs3"})
    _events(client, "hs3")  # paused at ``ask`` (a clarification is owed to the asker)
    resp = client.post("/handoff/select", json={"session_id": "hs3", "person_id": "E001"})
    assert resp.status_code == 409


def test_handoff_select_404_after_answered(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hs4"})
    _events(client, "hs4")
    client.post("/answer", json={"session_id": "hs4", "outcome": "accepted"})
    _events(client, "hs4")  # completed
    resp = client.post("/handoff/select", json={"session_id": "hs4", "person_id": "E002"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST /handoff/exclude : asker excludes the send target ("この人には聞かない"),
# rerouting to a freshly-scored next candidate and recording c6 feedback (#260)
# --------------------------------------------------------------------------- #
def test_handoff_exclude_reroutes_to_next_candidate(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    # Act as the asker (employee 10), not admin, so the recorded actor_id is real
    # (admin's principal has no employee_id).
    client.headers.update(_user_headers(10))
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hx1"})
    first = _events(client, "hx1")  # pause at send, drafted for E001
    assert first[2][1]["recommendations"][0]["person_id"] == "E001"

    # Asker excludes the current send target; the reroute is queued, not streamed
    # synchronously (the open /events stream picks it up), so this acks like /answer.
    resp = client.post("/handoff/exclude", json={"session_id": "hx1", "person_id": "E001"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "reroute_queued"

    # The next /events segment re-scores excluding E001 and re-drafts for E002 —
    # the same shape a responder decline produces, but asker-initiated.
    second = _events(client, "hx1")
    assert [e for e, _ in second] == ["recommend", "draft"]
    assert second[0][1]["recommendations"][0]["person_id"] == "E002"

    check = get_sessionmaker(engine)()
    try:
        # The exclusion is recorded as a c6 feedback signal (#237 Phase 1 table).
        fb = (
            check.query(Feedback).filter(Feedback.session_id == "hx1", Feedback.stage == "c6").all()
        )
        assert len(fb) == 1
        assert fb[0].kind == "person_excluded"
        assert fb[0].target == "E001"
        assert fb[0].actor_id == 10  # the asker (from the authenticated principal)

        # An asker exclusion is NOT a responder decline, but it still TERMINATES
        # E001's shown rank-1 row (outcome="excluded", not "declined" and not NULL)
        # so it stops showing as a pending hand-off — while never counting against
        # E001's acceptance rate the way "declined" would.
        q = check.query(Question).filter(Question.session_id == "hx1").first()
        e1_rows = (
            check.query(Recommendation)
            .filter(Recommendation.question_id == q.id, Recommendation.employee_id == 1)
            .all()
        )
        assert e1_rows and all(r.outcome == "excluded" for r in e1_rows)

        # Consequence: the excluded person no longer sees this session in their
        # /inbox; the rerouted-to candidate (E002) does (pending, outcome NULL).
        from tekijin.data.inbox import pending_handoffs_for_responder

        e1_inbox = pending_handoffs_for_responder(check, 1)
        e2_inbox = pending_handoffs_for_responder(check, 2)
        assert all(item["session_id"] != "hx1" for item in e1_inbox)
        assert any(item["session_id"] == "hx1" for item in e2_inbox)
    finally:
        check.close()

    # The rerouted hand-off still completes normally.
    assert (
        client.post("/answer", json={"session_id": "hx1", "outcome": "accepted"}).status_code == 200
    )


def test_handoff_exclude_rejects_non_primary_person(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hx2"})
    _events(client, "hx2")  # primary is E001
    # Only the send target may be excluded via the reroute path; a shown-but-not-
    # target candidate is rejected (422) rather than silently declining E001.
    resp = client.post("/handoff/exclude", json={"session_id": "hx2", "person_id": "E003"})
    assert resp.status_code == 422


def test_handoff_exclude_404_when_no_pending_handoff(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.post("/handoff/exclude", json={"session_id": "nope", "person_id": "E001"})
    assert resp.status_code == 404


def test_handoff_exclude_409_when_awaiting_clarification(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "hx3"})
    _events(client, "hx3")  # paused at ``ask`` (a clarification is owed to the asker)
    resp = client.post("/handoff/exclude", json={"session_id": "hx3", "person_id": "E001"})
    assert resp.status_code == 409


def test_handoff_exclude_404_after_answered(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hx4"})
    _events(client, "hx4")
    client.post("/answer", json={"session_id": "hx4", "outcome": "accepted"})
    _events(client, "hx4")  # completed
    resp = client.post("/handoff/exclude", json={"session_id": "hx4", "person_id": "E001"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST /handoff/redraft : asker regenerates the hand-off draft ("作り直し", #260)
# --------------------------------------------------------------------------- #
class _CountingDraft:
    """A draft model that tags each generation so a redraft is observably different
    (the deterministic TemplateDraftModel would return identical text)."""

    def __init__(self) -> None:
        self.n = 0

    def draft(self, question, top, asker, missing, *, situation, topics, known_values) -> str:
        self.n += 1
        return f"下書きv{self.n}（{top['name']}さんへ）"


def _redraft_client(engine, fake_embedder, draft_model) -> TestClient:
    service = AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=MemorySaver(),
        embedder=fake_embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=draft_model,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
        now_factory=lambda: NOW,
    )
    client = _app_client(service)
    client.headers.update(_user_headers(10))  # act as the asker for a real actor_id
    return client


def test_handoff_redraft_regenerates_draft(seed_counts, engine, fake_embedder) -> None:
    client = _redraft_client(engine, fake_embedder, _CountingDraft())
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "rd1"})
    first = _events(client, "rd1")
    first_drafts = [d["draft"] for e, d in first if e == "draft"]
    assert first_drafts and "下書きv1" in first_drafts[-1]

    resp = client.post("/handoff/redraft", json={"session_id": "rd1"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "redraft_queued"

    # The redraft segment re-runs C7 (not C6) and re-emits just a fresh draft, then
    # re-pauses at send — the same top candidate, a newly generated text.
    second = _events(client, "rd1")
    assert [e for e, _ in second] == ["draft"]
    assert "下書きv2" in second[0][1]["draft"]

    check = get_sessionmaker(engine)()
    try:
        # The regeneration is recorded as a c7 feedback signal with the discarded
        # draft in the payload.
        fb = (
            check.query(Feedback).filter(Feedback.session_id == "rd1", Feedback.stage == "c7").all()
        )
        assert len(fb) == 1
        assert fb[0].kind == "draft_regenerated"
        assert fb[0].actor_id == 10
        assert "下書きv1" in (fb[0].payload or {}).get("previous", "")
    finally:
        check.close()

    # The regenerated hand-off still completes normally.
    assert (
        client.post("/answer", json={"session_id": "rd1", "outcome": "accepted"}).status_code == 200
    )


def test_handoff_redraft_404_when_no_pending_handoff(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.post("/handoff/redraft", json={"session_id": "nope"})
    assert resp.status_code == 404


def test_handoff_redraft_409_when_awaiting_clarification(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "rd2"})
    _events(client, "rd2")  # paused at ``ask`` (a clarification is owed to the asker)
    resp = client.post("/handoff/redraft", json={"session_id": "rd2"})
    assert resp.status_code == 409


def test_handoff_redraft_404_after_answered(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "rd3"})
    _events(client, "rd3")
    client.post("/answer", json={"session_id": "rd3", "outcome": "accepted"})
    _events(client, "rd3")  # completed
    resp = client.post("/handoff/redraft", json={"session_id": "rd3"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST /handoff/correct : asker corrects the AI's interpretation ("解釈の訂正"),
# re-running the whole pipeline from C1 with an added supplement (#260)
# --------------------------------------------------------------------------- #
def test_handoff_correct_reruns_pipeline_from_c1(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.headers.update(_user_headers(10))  # act as the asker for a real actor_id
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hc1"})
    first = _events(client, "hc1")
    assert first[2][1]["recommendations"][0]["person_id"] == "E001"

    resp = client.post(
        "/handoff/correct",
        json={"session_id": "hc1", "supplement": "実は対象は5拠点で機器はUTMです"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reinterpret_queued"

    # The correction restarts the WHOLE pipeline (reset → C1 → … → send), so the
    # segment re-emits the full understood/route/recommend/draft sequence.
    second = [e for e, _ in _events(client, "hc1")]
    assert second == ["understood", "route", "recommend", "draft"]

    # The enriched question now drives the run (visible on the responder handoff).
    handoff = client.get("/handoff/hc1").json()
    assert "5拠点" in handoff["question"]

    check = get_sessionmaker(engine)()
    try:
        # The correction is recorded as a c1 feedback signal with the supplement.
        fb = (
            check.query(Feedback).filter(Feedback.session_id == "hc1", Feedback.stage == "c1").all()
        )
        assert len(fb) == 1
        assert fb[0].kind == "interpretation_corrected"
        assert fb[0].actor_id == 10
        assert "5拠点" in (fb[0].payload or {}).get("supplement", "")

        # The abandoned original hand-off row is terminated ("superseded") so it
        # stops showing as pending; the re-run's fresh row is the live one.
        q = check.query(Question).filter(Question.session_id == "hc1").first()
        e1_rows = (
            check.query(Recommendation)
            .filter(Recommendation.question_id == q.id, Recommendation.employee_id == 1)
            .order_by(Recommendation.id)
            .all()
        )
        assert any(r.outcome == "superseded" for r in e1_rows)
        assert e1_rows[-1].outcome is None  # the newest (re-run) row is live/pending
    finally:
        check.close()

    # The re-interpreted hand-off still completes normally.
    assert (
        client.post("/answer", json={"session_id": "hc1", "outcome": "accepted"}).status_code == 200
    )


def test_handoff_correct_rejects_blank_supplement(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "hc2"})
    _events(client, "hc2")
    resp = client.post("/handoff/correct", json={"session_id": "hc2", "supplement": "   "})
    assert resp.status_code == 422  # schema rejects a blank supplement


def test_handoff_correct_404_when_no_pending_handoff(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.post("/handoff/correct", json={"session_id": "nope", "supplement": "補足"})
    assert resp.status_code == 404


def test_handoff_correct_409_when_awaiting_clarification(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "hc3"})
    _events(client, "hc3")  # paused at ``ask`` (a clarification is owed to the asker)
    resp = client.post("/handoff/correct", json={"session_id": "hc3", "supplement": "補足"})
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# #268: an enriched question (clarification reply / interpretation correction)
# is persisted to Question.body so /inbox and /history match the processed run
# --------------------------------------------------------------------------- #
def _question_body(engine, session_id: str) -> str:
    with get_sessionmaker(engine)() as s:
        q = s.query(Question).filter(Question.session_id == session_id).first()
        return q.body if q else ""


def test_clarification_reply_persists_enriched_question_body(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": VAGUE_Q, "session_id": "qb1"})
    _events(client, "qb1")  # paused at ask (a clarification is owed)
    assert VAGUE_Q in _question_body(engine, "qb1")  # still the original so far

    client.post("/answer", json={"session_id": "qb1", "reply": "現行はVPN機器で3拠点です"})
    _events(client, "qb1")  # resumes; the ask node folds the reply into the question
    body = _question_body(engine, "qb1")
    assert "現行はVPN機器で3拠点です" in body  # #268: enriched body persisted for /inbox / /history


def test_correction_persists_enriched_question_body(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "qb2"})
    _events(client, "qb2")  # pause at send
    client.post("/handoff/correct", json={"session_id": "qb2", "supplement": "対象は5拠点です"})
    # The correction persists the enriched body durably, before the re-run streams.
    assert "対象は5拠点です" in _question_body(engine, "qb2")


def test_record_feedback_bounds_oversized_payload_values() -> None:
    """Internal callers stash drafts in ``payload``; an oversized string is
    truncated so the JSONB row cannot grow unbounded (bypasses the public 16KB
    schema cap) (#260 review MEDIUM)."""
    from tekijin.data.feedback import (
        _MAX_PAYLOAD_VALUE_CHARS,
        _TRUNCATION_MARK,
        _bounded_payload,
    )

    assert _bounded_payload(None) is None
    huge = "あ" * (_MAX_PAYLOAD_VALUE_CHARS + 500)
    out = _bounded_payload({"previous": huge, "kept": "short", "n": 3})
    assert out is not None
    assert out["previous"] == huge[:_MAX_PAYLOAD_VALUE_CHARS] + _TRUNCATION_MARK
    assert out["kept"] == "short"  # under the cap, untouched
    assert out["n"] == 3  # non-string values pass through


def test_set_recommendation_outcome_is_idempotent(seed_counts, session) -> None:
    from tekijin.data.writes import set_recommendation_outcome

    rec = Recommendation(question_id="q_0001", employee_id=3, rank=1, score=0.5, outcome=None)
    session.add(rec)
    session.flush()
    set_recommendation_outcome(session, rec.id, "accepted")
    set_recommendation_outcome(session, rec.id, "declined")  # must NOT overwrite
    session.flush()
    session.refresh(rec)
    assert rec.outcome == "accepted"  # first write wins; second is a no-op


def test_resume_reconciles_to_stored_outcome_after_restart(
    seed_counts, engine, fake_embedder
) -> None:
    # Divergence + stuck guard (#38 re-review): if a restart loses the in-memory
    # pending guard after the outcome was recorded, a second /answer with a
    # DIFFERENT action must neither overwrite the DB nor leave the graph stuck. The
    # stored outcome wins and the graph is resumed with it (consistent).
    svc = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2]),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    svc.start_question("dv", 10, GOOD_Q)
    list(svc.stream_events("dv"))  # pause at send
    svc.submit_resume("dv", outcome="accepted")  # records accepted + queues resume
    svc._registry.clear()  # simulate restart: pending guard lost, outcome persisted
    svc.submit_resume("dv", outcome="declined")  # resubmit a DIFFERENT action
    recs = _recs_for(engine, _latest_question(engine).id)
    assert recs[0].outcome == "accepted"  # first write wins; never overwritten
    # The graph advances consistently (resumed with the stored 'accepted'), not
    # left permanently paused at send.
    done = [ev.event for ev in svc.stream_events("dv")]
    assert "done" in done
    assert _recs_for(engine, _latest_question(engine).id)[0].outcome == "accepted"


def test_handoff_unexpected_error_is_generic_500(seed_counts, engine, fake_embedder) -> None:
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))

    def _boom(*_a, **_k):
        raise RuntimeError("secret internal detail at 10.0.0.1")

    service.get_handoff = _boom  # type: ignore[method-assign]
    client = _app_client(service)
    resp = client.get("/handoff/anything")
    assert resp.status_code == 500
    assert "内部エラー" in resp.text
    assert "secret internal" not in resp.text  # detail logged, never leaked


def test_responder_reuse_stats_counts_reuse_and_helpful(seed_counts, session) -> None:
    from tekijin.data.handoff import responder_reuse_stats

    before = responder_reuse_stats(session, 3)
    session.add_all(
        [
            Answer(
                id="h_ra_1", question_id="q_0001", responder_id=3, reuse_count=2, was_helpful=True
            ),
            Answer(
                id="h_ra_2", question_id="q_0001", responder_id=3, reuse_count=3, was_helpful=True
            ),
            Answer(
                id="h_ra_3",
                question_id="q_0001",
                responder_id=3,
                reuse_count=None,
                was_helpful=False,
            ),
        ]
    )
    session.flush()
    after = responder_reuse_stats(session, 3)
    # NULL reuse_count coalesces to 0; only was_helpful=True rows are counted.
    assert after["reuse_count"] - before["reuse_count"] == 5
    assert after["helpful_answer_count"] - before["helpful_answer_count"] == 2


def test_employee_brief_returns_name_and_dept(seed_counts, session) -> None:
    from tekijin.data.handoff import employee_brief

    name, _dept = employee_brief(session, 1)
    assert name  # employee 1 exists in the seed
    assert employee_brief(session, 999999) == (None, None)  # unknown -> both None


# --------------------------------------------------------------------------- #
# real HybridRetriever + real scorer smoke (no fakes) — flow completes
# --------------------------------------------------------------------------- #
def test_real_agent_smoke(seed_counts, engine, fake_embedder) -> None:
    # The default (real) retriever/scorer run over the committed seed. Embeddings
    # are NULL (dense returns nothing); BM25 over seed text still drives C4, so the
    # flow completes without a crash. We only assert the deterministic prefix.
    client = _client(engine, fake_embedder)  # default real retriever/scorer
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "real"})
    names = [e for e, _ in _events(client, "real")]
    assert names[0] == "understood"
    assert "route" in names


# --------------------------------------------------------------------------- #
# PostgresSaver smoke (persistence across graph instances) — pgserver/CI
# --------------------------------------------------------------------------- #
def test_postgres_checkpointer_persists(
    seed_counts, engine, fake_embedder, database_url, test_schema
) -> None:
    # Build a PostgresSaver whose connections use the run's ISOLATED schema, so
    # its checkpoint tables land there (dropped at teardown) rather than public.
    # The ``database_url`` fixture already skips when no DB is available; when a DB
    # IS present, a setup failure must fail the test (not be swallowed into skip).
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from tekijin.api.checkpointer import _postgres_conn_string

    def _use_schema(conn) -> None:
        conn.execute(f"SET search_path TO {test_schema}, public")

    pool = ConnectionPool(
        _postgres_conn_string(database_url),
        min_size=1,
        max_size=2,
        timeout=5.0,
        open=True,
        configure=_use_schema,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    try:
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()

        client = _client(
            engine,
            fake_embedder,
            retriever=_FakeRetriever(people=[1], people_confidence=0.2),
            scorer=_FakeScorer(_recs(1)),
            checkpointer=checkpointer,
        )
        client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "pg1"})
        first = [e for e, _ in _events(client, "pg1")]
        assert first == ["understood", "route", "recommend", "draft"]

        # A brand-new client (fresh graph) sharing the SAME postgres checkpointer
        # resumes the paused run from durable state.
        client2 = _client(
            engine,
            fake_embedder,
            retriever=_FakeRetriever(people=[1], people_confidence=0.2),
            scorer=_FakeScorer(_recs(1)),
            checkpointer=checkpointer,
        )
        client2.post("/answer", json={"session_id": "pg1", "outcome": "accepted"})
        assert _events(client2, "pg1")[0][0] == "done"
    finally:
        pool.close()


# --------------------------------------------------------------------------- #
# service-level helper for durability / concurrency tests (no TestClient/SSE)
# --------------------------------------------------------------------------- #
def _svc(
    engine,
    embedder,
    *,
    retriever=None,
    scorer=None,
    now_factory=None,
    answerability_model=None,
    answerability_threshold=40,
) -> AgentService:
    return AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=MemorySaver(),
        embedder=embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=TemplateDraftModel(),
        answerability_model=answerability_model,
        answerability_threshold=answerability_threshold,
        retriever=retriever,
        scorer=scorer,
        now_factory=now_factory or (lambda: NOW),
    )


def _latest_question(engine) -> Question:
    check = get_sessionmaker(engine)()
    try:
        q = (
            check.query(Question)
            .filter(Question.body == GOOD_Q)
            .order_by(Question.created_at.desc())
            .first()
        )
        assert q is not None
        return q
    finally:
        check.close()


def _recs_for(engine, question_id: str) -> list[Recommendation]:
    check = get_sessionmaker(engine)()
    try:
        return (
            check.query(Recommendation)
            .filter(Recommendation.question_id == question_id)
            .order_by(Recommendation.rank)
            .all()
        )
    finally:
        check.close()


# --------------------------------------------------------------------------- #
# asker validation (404) / boundary coercion / path-safety
# --------------------------------------------------------------------------- #
def test_ask_unknown_asker_is_404(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    resp = client.post("/ask", json={"asker_id": 999999, "question": GOOD_Q, "session_id": "u404"})
    assert resp.status_code == 404  # clean boundary error, not a mid-flush FK 500


def test_ask_accepts_e_prefixed_asker_and_stores_int(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    resp = client.post("/ask", json={"asker_id": "E10", "question": GOOD_Q, "session_id": "ep"})
    assert resp.status_code == 200
    assert _latest_question(engine).asker_id == 10  # "E10" normalised to int 10


def test_session_id_must_be_path_safe(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    # A '/' would make GET /events/{session_id} unroutable — reject at the boundary.
    assert (
        client.post(
            "/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "a/b"}
        ).status_code
        == 422
    )
    assert (
        client.post("/answer", json={"session_id": "a b", "outcome": "accepted"}).status_code == 422
    )


# --------------------------------------------------------------------------- #
# route-level exception guards: unexpected errors become a generic 500
# --------------------------------------------------------------------------- #
def test_ask_unexpected_error_is_generic_500(seed_counts, engine, fake_embedder) -> None:
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))

    def _boom(*_a, **_k):
        raise RuntimeError("secret internal detail at 10.0.0.1")

    service.start_question = _boom  # type: ignore[method-assign]
    client = _app_client(service)
    resp = client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "b"})
    assert resp.status_code == 500
    assert "内部エラー" in resp.text
    assert "secret internal" not in resp.text  # detail logged, never leaked


def test_answer_unexpected_error_is_generic_500(seed_counts, engine, fake_embedder) -> None:
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))

    def _boom(*_a, **_k):
        raise RuntimeError("secret internal detail at 10.0.0.1")

    service.submit_resume = _boom  # type: ignore[method-assign]
    client = _app_client(service)
    resp = client.post("/answer", json={"session_id": "b", "outcome": "accepted"})
    assert resp.status_code == 500
    assert "secret internal" not in resp.text


@pytest.mark.parametrize(
    ("path", "symbol"),
    [
        ("/dashboard", "dashboard_summary"),
        ("/inbox?responder_id=1", "pending_handoffs_for_responder"),
        ("/questions?asker_id=1", "recent_questions_for_asker"),
        ("/documents/doc_anything", "get_document"),
    ],
)
def test_read_endpoint_unexpected_error_is_generic_500(
    path, symbol, seed_counts, engine, fake_embedder, monkeypatch
) -> None:
    # #146: read endpoints must mask an unexpected error as a generic 500 (logged,
    # never leaked), matching /ask, /answer, /handoff. A VALID id is passed so the
    # request reaches the data call rather than short-circuiting on 422.
    import tekijin.api.routes as routes

    def _boom(*_a, **_k):
        raise RuntimeError("secret internal detail at 10.0.0.1")

    monkeypatch.setattr(routes, symbol, _boom)
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.get(path)
    assert resp.status_code == 500
    assert "内部エラー" in resp.text
    assert "secret internal" not in resp.text  # detail logged, never leaked


def test_employees_unexpected_error_is_generic_500(
    seed_counts, engine, fake_embedder, monkeypatch
) -> None:
    # /employees builds its repo inline, so boom via a fake Repository (#146).
    import tekijin.api.routes as routes

    class _BoomRepo:
        def __init__(self, *_a, **_k) -> None:
            pass

        def list_employees(self):
            raise RuntimeError("secret internal detail at 10.0.0.1")

    monkeypatch.setattr(routes, "Repository", _BoomRepo)
    client = _client(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    resp = client.get("/employees")
    assert resp.status_code == 500
    assert "内部エラー" in resp.text
    assert "secret internal" not in resp.text


# --------------------------------------------------------------------------- #
# backpressure: shed NEW questions with 503 when the run pool is saturated (#180)
# --------------------------------------------------------------------------- #
def test_run_slot_tracks_active_runs(seed_counts, engine, fake_embedder) -> None:
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    assert service._active_runs == 0
    with service._run_slot():
        assert service._active_runs == 1
        with service._run_slot():  # nesting (e.g. a concurrent run)
            assert service._active_runs == 2
        assert service._active_runs == 1
    assert service._active_runs == 0  # released by the finally


def test_ask_sheds_with_503_when_saturated(seed_counts, engine, fake_embedder) -> None:
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    service._max_concurrent_runs = 1
    service._active_runs = 1  # a run is already executing → pool full
    client = _app_client(service)
    resp = client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "busy1"})
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "5"
    assert "混雑" in resp.text


def test_ask_admission_runs_before_db_lookup(seed_counts, engine, fake_embedder) -> None:
    # Saturation is checked before asker validation and before any persist, so even a
    # bad asker_id sheds with 503 (not 404) — proving no DB work on a shed request.
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    service._max_concurrent_runs = 1
    service._active_runs = 1
    client = _app_client(service)
    resp = client.post("/ask", json={"asker_id": 999999, "question": GOOD_Q, "session_id": "busy2"})
    assert resp.status_code == 503


def test_ask_not_shed_when_backpressure_disabled(seed_counts, engine, fake_embedder) -> None:
    service = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    service._max_concurrent_runs = 0  # 0 disables the gate
    service._active_runs = 100
    client = _app_client(service)
    resp = client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "ok1"})
    assert resp.status_code == 200


def test_active_runs_released_after_a_full_stream(seed_counts, engine, fake_embedder) -> None:
    # A real ask→events flow must return the slot to zero once the stream completes.
    service = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    service._max_concurrent_runs = 4
    client = _app_client(service)
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "s_bp"})
    _events(client, "s_bp")  # drain the stream fully
    assert service._active_runs == 0


def test_run_slot_released_when_stream_closed_early(seed_counts, engine, fake_embedder) -> None:
    # A client that drops mid-stream: closing the generator (GeneratorExit) must run
    # the finally and release the slot — the disconnect-release path (#180 review).
    service = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2, 3], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2, 3)),
    )
    service.start_question("s_close", 10, GOOD_Q)
    gen = service.stream_events("s_close")
    assert next(gen).event == "understood"  # execution started → slot held
    assert service._active_runs == 1
    gen.close()  # simulate a dropped client
    assert service._active_runs == 0  # finally released the slot


def test_run_slot_released_on_exception(seed_counts, engine, fake_embedder) -> None:
    # The slot's finally must fire on the exception path, not just normal completion.
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    with pytest.raises(RuntimeError, match="boom"), service._run_slot():
        assert service._active_runs == 1
        raise RuntimeError("boom")
    assert service._active_runs == 0


def test_resume_not_shed_when_saturated(seed_counts, engine, fake_embedder) -> None:
    # Backpressure gates only NEW questions; a resume reaches submit_resume even while
    # saturated — a missing paused run is a 409, never a 503 (locks in the semantics).
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    service._max_concurrent_runs = 1
    service._active_runs = 1  # saturated
    client = _app_client(service)
    resp = client.post("/answer", json={"session_id": "no_such", "outcome": "accepted"})
    assert resp.status_code == 409  # SessionConflict, NOT 503


def test_ask_admitted_when_below_limit(seed_counts, engine, fake_embedder) -> None:
    # Enabled but not saturated (active < max) → still admitted.
    service = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    service._max_concurrent_runs = 2
    service._active_runs = 1  # below the limit
    client = _app_client(service)
    resp = client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "under1"})
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# durability: outcome recorded from the DURABLE state (registry-independent)
# --------------------------------------------------------------------------- #
def test_outcome_recorded_after_registry_cleared(seed_counts, engine, fake_embedder) -> None:
    # Simulates a restart / eviction: the volatile registry is gone, but /answer
    # still records the outcome by reading primary_recommendation_id from the
    # durable checkpoint state.
    svc = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2]),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    svc.start_question("dur", 10, GOOD_Q)
    list(svc.stream_events("dur"))  # runs to the send interrupt; primary id in state
    svc._registry.clear()  # <- registry no longer knows anything about "dur"
    svc.submit_resume("dur", outcome="accepted")
    recs = _recs_for(engine, _latest_question(engine).id)
    assert recs[0].rank == 1 and recs[0].outcome == "accepted"  # primary recorded
    assert recs[1].outcome is None


def test_disconnect_after_recommend_then_continue_and_outcome(
    seed_counts, engine, fake_embedder
) -> None:
    # Client disconnects right after C6 (recs persisted, but update_state — which
    # writes the primary id into the state — never ran). Reconnect CONTINUES the
    # parked run to the send interrupt (codex#6), and /answer still records the
    # outcome via the DB fallback (latest rank-1 for the question).
    svc = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2]),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    svc.start_question("mid", 10, GOOD_Q)
    gen = svc.stream_events("mid")
    seen: list[str | None] = []
    for ev in gen:
        seen.append(ev.event)
        if ev.event == "recommend":
            break
    gen.close()  # type: ignore[attr-defined]  # disconnect mid-run (parked node)
    assert "recommend" in seen

    rest = [ev.event for ev in svc.stream_events("mid")]  # continue to completion
    assert "draft" in rest

    svc.submit_resume("mid", outcome="accepted")
    recs = _recs_for(engine, _latest_question(engine).id)
    assert [r.rank for r in recs] == [1, 2]  # NOT double-inserted on continuation
    assert recs[0].outcome == "accepted"  # recorded via durable DB fallback


class _FlakyAnswerability:
    """#70 critic that fails once (transient vLLM error) then accepts — to exercise
    the reconnect-after-error path where C6 already committed but the critic had
    not run in the failed segment."""

    def __init__(self, confidence: int) -> None:
        self._confidence = confidence
        self.calls = 0

    def assess(self, question, candidate_evidence):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient critic error")
        from tekijin.agent.protocols import AnswerabilityResult

        return AnswerabilityResult(confidence=self._confidence, reason="ok")


def test_answerability_reconnect_after_critic_error_persists(
    seed_counts, engine, fake_embedder
) -> None:
    # CRITICAL guard (#70 review): the critic raises AFTER C6 has checkpointed
    # (next=answerability) but before it accepted, so the run parks at the
    # `answerability` node with the deferred recs NOT yet persisted. On reconnect,
    # a NEW _run segment resumes at `answerability` with empty pending_* locals —
    # the fix re-derives the shown recs from durable state so the accepted hand-off
    # is still persisted + surfaced (else the outcome record is silently lost).
    critic = _FlakyAnswerability(confidence=85)
    svc = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2]),
        scorer=_FakeScorer(_recs(1, 2)),
        answerability_model=critic,
    )
    svc.start_question("recon", 10, GOOD_Q)
    first = [ev.event for ev in svc.stream_events("recon")]
    assert "error" in first  # critic raised -> parked at answerability, no persist yet
    q = _latest_question(engine)
    assert _recs_for(engine, q.id) == []  # nothing persisted on the failed segment

    # Reconnect: resumes at answerability; the critic now accepts.
    second = [ev.event for ev in svc.stream_events("recon")]
    assert "recommend" in second and "draft" in second  # released on the resumed segment
    recs = _recs_for(engine, q.id)
    assert [r.rank for r in recs] == [1, 2]  # persisted despite the mid-run error

    svc.submit_resume("recon", outcome="accepted")
    assert _recs_for(engine, q.id)[0].outcome == "accepted"  # outcome recorded, not lost


# --------------------------------------------------------------------------- #
# eviction never drops a paused (mid-interrupt) session
# --------------------------------------------------------------------------- #
def test_sweep_protects_paused_and_evicts_idle(seed_counts, engine, fake_embedder) -> None:
    svc = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    svc.start_question("keep", 10, GOOD_Q)
    list(svc.stream_events("keep"))  # paused at send
    _ = svc._lock("idle")  # materialise the idle session's lock so we can watch GC
    # "Stale" is measured against the SAME injected clock the sweep uses (#0): a
    # magic 0.0 wrongly reads as "recent" on a freshly-booted CI runner.
    stale = svc._clock() - SESSION_TTL_SECONDS - 1
    svc._registry["keep"].touched_at = stale  # make the paused session look stale
    svc._registry["idle"] = _SessionCtx(pending=None, touched_at=stale)  # stale, no run
    svc._sweep()
    assert "keep" in svc._registry  # protected: a human is being waited on
    assert "idle" not in svc._registry  # evicted: stale and not mid-interrupt
    assert "idle" not in svc._locks  # its per-session lock was GC'd too (codex#2)


# --------------------------------------------------------------------------- #
# concurrency: the per-session lock serialises accept / resume / stream
# --------------------------------------------------------------------------- #
def test_concurrent_ask_single_winner(seed_counts, engine, fake_embedder) -> None:
    import concurrent.futures as cf

    svc = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )

    def _fire() -> int:
        try:
            svc.start_question("cc", 10, GOOD_Q)
            return 200
        except SessionConflict:
            return 409

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        results = sorted(f.result() for f in [ex.submit(_fire), ex.submit(_fire)])
    assert results == [200, 409]  # exactly one accepted
    check = get_sessionmaker(engine)()
    try:
        assert check.query(Question).filter(Question.body == GOOD_Q).count() == 1
    finally:
        check.close()


def test_concurrent_events_no_double_insert(seed_counts, engine, fake_embedder) -> None:
    import concurrent.futures as cf

    svc = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2]),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    svc.start_question("ce", 10, GOOD_Q)

    def _drain() -> tuple[str | None, ...]:
        return tuple(ev.event for ev in svc.stream_events("ce"))

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        a = ex.submit(_drain)
        b = ex.submit(_drain)
        streams = {a.result(), b.result()}
    full = ("understood", "route", "recommend", "draft")
    # One streamed the whole run; the other blocked on the lock, then reconnected
    # at the send interrupt — which now replays recommend + draft (#38 re-review).
    assert streams == {full, ("recommend", "draft")}
    assert len(_recs_for(engine, _latest_question(engine).id)) == 2  # inserted once


def test_concurrent_answer_single_winner(seed_counts, engine, fake_embedder) -> None:
    import concurrent.futures as cf

    svc = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    svc.start_question("ca", 10, GOOD_Q)
    list(svc.stream_events("ca"))  # pause at send

    def _fire() -> str:
        try:
            svc.submit_resume("ca", outcome="accepted")
            return "ok"
        except SessionConflict:
            return "conflict"

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        results = sorted(f.result() for f in [ex.submit(_fire), ex.submit(_fire)])
    assert results == ["conflict", "ok"]  # second resume rejected (no double-queue)
    recs = _recs_for(engine, _latest_question(engine).id)
    assert len([r for r in recs if r.outcome == "accepted"]) == 1  # recorded once


def test_no_candidate_persists_no_recommendation(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1], people_confidence=0.2),
        scorer=_FakeScorer([]),  # C6 runs but yields nothing -> no_candidate
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "nc"})
    names = [e for e, _ in _events(client, "nc")]
    assert "message" in names  # no_candidate terminal
    assert _recs_for(engine, _latest_question(engine).id) == []  # nothing persisted


def test_double_queued_resume_conflicts(seed_counts, engine, fake_embedder) -> None:
    svc = _svc(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    svc.start_question("dq", 10, GOOD_Q)
    list(svc.stream_events("dq"))  # pause at send
    svc.submit_resume("dq", outcome="accepted")  # queues a resume (not yet streamed)
    with pytest.raises(SessionConflict):
        svc.submit_resume("dq", outcome="declined")  # second while queued -> 409


def test_record_outcome_warns_when_no_target(seed_counts, engine, fake_embedder, caplog) -> None:
    # Defensive branch: an outcome with no recommendation to attach it to (no id in
    # state and none in the DB) logs a warning instead of silently dropping data.
    import logging

    svc = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))
    with caplog.at_level(logging.WARNING):
        svc._record_outcome("orphan", {}, "accepted")
    assert "no recommendation to record outcome" in caplog.text


# --------------------------------------------------------------------------- #
# codex#3: a finished run replays its terminal event on reconnect
# --------------------------------------------------------------------------- #
def test_reconnect_replays_done_after_completion(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "td"})
    _events(client, "td")  # to the send interrupt
    client.post("/answer", json={"session_id": "td", "outcome": "accepted"})
    done = _events(client, "td")
    assert [e for e, _ in done] == ["done"]

    again = _events(client, "td")  # reconnect AFTER completion
    assert [e for e, _ in again] == ["done"]  # terminal replayed (read-only)
    assert again[0][1] == done[0][1]  # identical payload
    # replay must not re-run the graph / re-insert recommendations.
    assert len(_recs_for(engine, _latest_question(engine).id)) == 1


def test_reconnect_replays_terminal_message(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1], people_confidence=0.2),
        scorer=_FakeScorer([]),  # no_candidate terminal
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "tmsg"})
    first = [e for e, _ in _events(client, "tmsg")]
    assert first[-1] == "message"
    again = _events(client, "tmsg")
    assert [e for e, _ in again] == ["message"]  # terminal message replayed


# --------------------------------------------------------------------------- #
# codex#4: Recommendation.created_at is the generation time, not the /ask time
# --------------------------------------------------------------------------- #
def test_reroute_created_at_is_generation_time_not_ask_time(
    seed_counts, engine, fake_embedder
) -> None:
    svc = _svc(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    svc.start_question("rt", 10, GOOD_Q)
    list(svc.stream_events("rt"))  # first C6 pass
    svc.submit_resume("rt", outcome="declined")
    list(svc.stream_events("rt"))  # reroute -> a later C6 pass inserts another row

    times = [r.created_at for r in _recs_for(engine, _latest_question(engine).id)]
    # DB server_default(now()) stamps the real insert time — never the injected
    # /ask NOW (which is a fixed future date in these tests).
    assert all(t != NOW for t in times)
    assert max(times) > min(times)  # the reroute pass was inserted strictly later


# --------------------------------------------------------------------------- #
# GET /notifications, POST /notifications/ack : decline notifications (#E7)
# --------------------------------------------------------------------------- #
def test_notifications_lists_decline_then_ack_clears_it(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "nt1"})
    _events(client, "nt1")  # pause at send for E001
    assert client.get("/notifications", params={"asker_id": "E010"}).json()["items"] == []

    client.post("/answer", json={"session_id": "nt1", "outcome": "declined"})
    _events(client, "nt1")  # reroute (auto-advances to E002) -> pause at send again

    body = client.get("/notifications", params={"asker_id": "E010"}).json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    # The declined person's real (seeded) name, not the _FakeScorer's fixture
    # name — Recommendation rows only ever store employee_id in the DB.
    assert item["declined_person_name"]
    assert f"{item['declined_person_name']}さんに断られた" in item["message"]
    assert item["session_id"] == "nt1"

    ack = client.post("/notifications/ack", json={"asker_id": "E010", "ids": [item["id"]]})
    assert ack.status_code == 200
    assert ack.json()["acknowledged"] == 1

    assert client.get("/notifications", params={"asker_id": "E010"}).json()["items"] == []
    # Re-acking an already-seen id is a harmless no-op.
    again = client.post("/notifications/ack", json={"asker_id": "E010", "ids": [item["id"]]})
    assert again.json()["acknowledged"] == 0


def test_notifications_scoped_to_the_owning_asker(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "nt2"})
    _events(client, "nt2")
    client.post("/answer", json={"session_id": "nt2", "outcome": "declined"})
    _events(client, "nt2")

    # A different asker cannot see or ack someone else's decline notification.
    assert client.get("/notifications", params={"asker_id": "E011"}).json()["items"] == []
    mine = client.get("/notifications", params={"asker_id": "E010"}).json()["items"]
    notif_id = mine[0]["id"]
    other_ack = client.post("/notifications/ack", json={"asker_id": "E011", "ids": [notif_id]})
    assert other_ack.json()["acknowledged"] == 0
    assert len(client.get("/notifications", params={"asker_id": "E010"}).json()["items"]) == 1


# --------------------------------------------------------------------------- #
# #207: delete a past question (and its FK children), owner/admin only
# --------------------------------------------------------------------------- #
def _insert_question(engine, qid: str, asker_id: int, *, with_children: bool = False) -> None:
    """Directly seed one ``api_``-prefixed question (optionally with FK children)
    so the delete tests do not depend on driving the whole SSE flow."""

    factory = get_sessionmaker(engine)
    with factory() as s:
        s.add(
            Question(
                id=qid,
                asker_id=asker_id,
                body="削除テスト用の質問",
                topics=[],
                status="open",
                created_at=NOW,
                session_id=None,
            )
        )
        s.flush()  # the question must exist before its FK children insert
        if with_children:
            s.add(Answer(id=f"{qid}_a", question_id=qid, responder_id=1, body="回答本文"))
            rec = Recommendation(question_id=qid, employee_id=1, rank=1, score=0.9)
            s.add(rec)
            s.add(Event(question_id=qid, stage="c1", started_at=NOW, ended_at=NOW, meta=None))
            s.flush()  # rec.id (autoincrement) must exist before the message FKs it
            s.add(Message(recommendation_id=rec.id, sender_id=1, body="チャット本文"))
            # A learning signal keyed to this question. Every correction the asker
            # makes (draft edits, person exclusions, re-runs) writes one of these.
            s.add(
                Feedback(
                    question_id=qid,
                    stage="c7",
                    kind="draft_edited",
                    payload={"generated": "旧", "sent": "新"},
                    actor_id=asker_id,
                )
            )
        s.commit()


def _counts_for(engine, qid: str) -> tuple[int, int, int, int, int, int]:
    factory = get_sessionmaker(engine)
    with factory() as s:
        recommendation_ids = [
            rec_id
            for (rec_id,) in s.query(Recommendation.id)
            .filter(Recommendation.question_id == qid)
            .all()
        ]
        message_count = (
            s.query(Message).filter(Message.recommendation_id.in_(recommendation_ids)).count()
            if recommendation_ids
            else 0
        )
        return (
            s.query(Question).filter(Question.id == qid).count(),
            s.query(Answer).filter(Answer.question_id == qid).count(),
            len(recommendation_ids),
            s.query(Event).filter(Event.question_id == qid).count(),
            message_count,
            s.query(Feedback).filter(Feedback.question_id == qid).count(),
        )


def test_delete_question_removes_it_and_its_children(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)  # admin
    _insert_question(engine, "api_del1", 10, with_children=True)
    # question, answer, recommendation, event, and its chat message all exist.
    assert _counts_for(engine, "api_del1") == (1, 1, 1, 1, 1, 1)

    resp = client.delete("/questions/api_del1")
    assert resp.status_code == 200
    assert resp.json() == {"question_id": "api_del1", "deleted": True}
    # question and ALL its FK children — including chat messages, one hop out
    # via the recommendation, and the feedback rows (#286) — are gone.
    assert _counts_for(engine, "api_del1") == (0, 0, 0, 0, 0, 0)


def test_owner_can_delete_their_own_question(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    _insert_question(engine, "api_del2", 10)
    resp = client.delete("/questions/api_del2", headers=_user_headers(10))
    assert resp.status_code == 200
    assert _counts_for(engine, "api_del2")[0] == 0


def test_delete_question_forbidden_for_non_owner(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    _insert_question(engine, "api_del3", 10)
    # employee 11 is not the asker and not an admin -> 403, row untouched.
    resp = client.delete("/questions/api_del3", headers=_user_headers(11))
    assert resp.status_code == 403
    assert _counts_for(engine, "api_del3")[0] == 1


def test_delete_missing_question_returns_404(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    resp = client.delete("/questions/api_does_not_exist")
    assert resp.status_code == 404


def test_deleted_question_drops_out_of_recent_list(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    _insert_question(engine, "api_del4", 10)
    before = client.get("/questions", params={"asker_id": "E010"}).json()["items"]
    assert any(i["question_id"] == "api_del4" for i in before)

    client.delete("/questions/api_del4")
    after = client.get("/questions", params={"asker_id": "E010"}).json()["items"]
    assert all(i["question_id"] != "api_del4" for i in after)


# --------------------------------------------------------------------------- #
# #159: POST /questions/{id}/resolve — the asker marks a question self-resolved
# (人を介さず解決した), feeding the dashboard self-resolution rate
# --------------------------------------------------------------------------- #
def _set_route(engine, qid: str, route: str) -> None:
    from sqlalchemy import update

    with get_sessionmaker(engine)() as s:
        s.execute(update(Question).where(Question.id == qid).values(route=route))
        s.commit()


def test_resolve_marks_self_resolution_and_shows_in_history(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(engine, fake_embedder)
    _insert_question(engine, "api_res1", 10)
    resp = client.post("/questions/api_res1/resolve", headers=_user_headers(10))
    assert resp.status_code == 200 and resp.json() == {"question_id": "api_res1", "resolved": True}

    with get_sessionmaker(engine)() as s:
        q = s.query(Question).filter(Question.id == "api_res1").first()
        assert q.resolution_kind == "self" and q.resolved_at == NOW

    # The asker's history now labels it a self-resolution.
    item = next(
        i
        for i in client.get("/questions", params={"asker_id": "E010"}).json()["items"]
        if i["question_id"] == "api_res1"
    )
    assert item["resolution"] == "self" and item["resolved"] is True


def test_resolve_is_idempotent_and_first_wins(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    _insert_question(engine, "api_res2", 10)
    assert client.post("/questions/api_res2/resolve", headers=_user_headers(10)).status_code == 200
    # Re-marking is a no-op that still acks; resolved_at is not moved.
    assert client.post("/questions/api_res2/resolve", headers=_user_headers(10)).status_code == 200
    with get_sessionmaker(engine)() as s:
        q = s.query(Question).filter(Question.id == "api_res2").first()
        assert q.resolution_kind == "self" and q.resolved_at == NOW


def test_resolve_forbidden_for_non_owner(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    _insert_question(engine, "api_res3", 10)
    resp = client.post("/questions/api_res3/resolve", headers=_user_headers(11))
    assert resp.status_code == 403
    with get_sessionmaker(engine)() as s:
        q = s.query(Question).filter(Question.id == "api_res3").first()
        assert q.resolution_kind is None  # untouched


def test_resolve_missing_question_returns_404(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    assert client.post("/questions/api_missing/resolve").status_code == 404


def test_self_resolution_counts_in_dashboard_rate(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    _insert_question(engine, "api_res4", 10)
    _set_route(engine, "api_res4", "send")  # a routed, person-bound question (real C5 route)
    with get_sessionmaker(engine)() as s:
        before = dashboard_summary(s)["self_resolution_rate"]
    client.post("/questions/api_res4/resolve", headers=_user_headers(10))
    with get_sessionmaker(engine)() as s:
        after = dashboard_summary(s)["self_resolution_rate"]
    # The explicit self-resolution counts in the numerator: the question was routed
    # to a person but the asker solved it WITHOUT anyone actually answering.
    assert after > before


def test_person_answered_question_is_not_counted_self_even_if_marked(
    seed_counts, engine, fake_embedder
) -> None:
    """A question a person actually resolved must never count as self-resolved, even
    if the asker also clicked "自分で解決した" (race: self-resolve while pending, then a
    responder accepts). The dashboard mirrors history's person>self precedence (#159
    review HIGH)."""
    from sqlalchemy import update

    client = _client(engine, fake_embedder)
    _insert_question(engine, "api_res5", 10)
    _set_route(engine, "api_res5", "send")
    # The asker self-resolves while the hand-off is still pending.
    client.post("/questions/api_res5/resolve", headers=_user_headers(10))
    with get_sessionmaker(engine)() as s:
        after_self = dashboard_summary(s)["self_resolution_rate"]
    # A responder's recommendation is THEN accepted (a real person resolution).
    with get_sessionmaker(engine)() as s:
        rec = Recommendation(
            question_id="api_res5", employee_id=1, rank=1, score=0.5, outcome="accepted"
        )
        s.add(rec)
        s.commit()
    with get_sessionmaker(engine)() as s:
        after_person = dashboard_summary(s)["self_resolution_rate"]
        # The self-mark row remains, but the metric no longer counts it as self.
        q = s.query(Question).filter(Question.id == "api_res5").first()
        assert q.resolution_kind == "self"  # the label persists (history shows person)
    assert after_person < after_self  # the accepted person-resolution removed it from self

    # And guarding the write path: a question ALREADY person-resolved (resolved_at
    # set) cannot be re-labelled self.
    _insert_question(engine, "api_res6", 10)
    with get_sessionmaker(engine)() as s:
        s.execute(update(Question).where(Question.id == "api_res6").values(resolved_at=NOW))
        s.commit()
    client.post("/questions/api_res6/resolve", headers=_user_headers(10))
    with get_sessionmaker(engine)() as s:
        q = s.query(Question).filter(Question.id == "api_res6").first()
        assert q.resolution_kind is None  # guard held: not re-labelled self


# --------------------------------------------------------------------------- #
# #208: /questions honours a `limit` (history screen requests many, panel few)
# --------------------------------------------------------------------------- #
def test_questions_limit_caps_and_orders_newest_first(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    # Seven questions strictly newer than any seeded row (created_at in the future),
    # so they are deterministically the newest regardless of seed history.
    factory = get_sessionmaker(engine)
    with factory() as s:
        for i in range(7):
            s.add(
                Question(
                    id=f"api_hist{i}",
                    asker_id=10,
                    body=f"履歴テスト{i}",
                    topics=[],
                    status="open",
                    created_at=NOW + dt.timedelta(minutes=i),
                    session_id=None,
                )
            )
        s.commit()

    # limit=3 -> exactly the 3 newest (hist6, hist5, hist4), newest first.
    got = client.get("/questions", params={"asker_id": "E010", "limit": 3}).json()["items"]
    assert [i["question_id"] for i in got] == ["api_hist6", "api_hist5", "api_hist4"]

    # A larger limit returns more of them (all 7 api_ ones are newest).
    many = client.get("/questions", params={"asker_id": "E010", "limit": 50}).json()["items"]
    top7 = [i["question_id"] for i in many[:7]]
    assert top7 == [f"api_hist{i}" for i in range(6, -1, -1)]


def test_questions_limit_is_validated(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    # Out-of-range limits are rejected (1..200).
    assert client.get("/questions", params={"asker_id": "E010", "limit": 0}).status_code == 422
    assert client.get("/questions", params={"asker_id": "E010", "limit": 201}).status_code == 422


# --------------------------------------------------------------------------- #
# #237 Phase 1: record feedback (the correction signal the runtime discarded)
# --------------------------------------------------------------------------- #
def _feedback_rows(engine) -> list[Feedback]:
    with get_sessionmaker(engine)() as s:
        return s.query(Feedback).order_by(Feedback.id).all()


def test_feedback_endpoint_records_with_actor_from_principal(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(engine, fake_embedder)
    resp = client.post(
        "/feedback",
        json={
            "stage": "c6",
            "kind": "person_rejected",
            "target": "E001",
            "payload": {"reason": "多忙"},
        },
        headers=_user_headers(10),
    )
    assert resp.status_code == 200 and resp.json() == {"status": "recorded"}
    rows = _feedback_rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row.stage == "c6" and row.kind == "person_rejected" and row.target == "E001"
    assert row.payload == {"reason": "多忙"}
    # actor_id comes from the token (employee 10), NOT the body.
    assert row.actor_id == 10


def test_feedback_owner_may_tag_their_question_stranger_gets_403(
    seed_counts, engine, fake_embedder
) -> None:
    # q_0001 is owned by asker_id 33 (fixtures). Object-level auth (#263).
    client = _client(engine, fake_embedder)
    owner = client.post(
        "/feedback",
        json={"stage": "c1", "kind": "x", "question_id": "q_0001"},
        headers=_user_headers(33),
    )
    assert owner.status_code == 200
    # A non-owner cannot tag someone else's question (would pollute its signal).
    stranger = client.post(
        "/feedback",
        json={"stage": "c1", "kind": "x", "question_id": "q_0001"},
        headers=_user_headers(10),
    )
    assert stranger.status_code == 403
    # Only the owner's row was recorded, with the link kept.
    rows = _feedback_rows(engine)
    assert len(rows) == 1 and rows[0].actor_id == 33 and rows[0].question_id == "q_0001"


def test_feedback_unknown_question_id_is_dropped_not_403(
    seed_counts, engine, fake_embedder
) -> None:
    # An unknown id must not 403 (that would be an existence oracle) nor 500 (FK) —
    # the link is dropped and the signal still recorded (#263, preserves #237).
    client = _client(engine, fake_embedder)
    resp = client.post(
        "/feedback",
        json={"stage": "c1", "kind": "x", "question_id": "q_does_not_exist"},
        headers=_user_headers(10),
    )
    assert resp.status_code == 200
    rows = _feedback_rows(engine)
    assert len(rows) == 1 and rows[0].question_id is None


def test_feedback_non_participant_cannot_tag_a_live_session(
    seed_counts, engine, fake_embedder
) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "fbauth"})
    _events(client, "fbauth")  # live session, asker=10 / responder=E001
    # A stranger (not asker/responder) cannot tag the live session.
    stranger = client.post(
        "/feedback",
        json={"stage": "c6", "kind": "x", "session_id": "fbauth"},
        headers=_user_headers(999),
    )
    assert stranger.status_code == 403
    # The asker can.
    ok = client.post(
        "/feedback",
        json={"stage": "c6", "kind": "x", "session_id": "fbauth"},
        headers=_user_headers(10),
    )
    assert ok.status_code == 200


def test_feedback_is_rate_limited_per_actor(seed_counts, engine, fake_embedder) -> None:
    from tekijin.api.rate_limit import SlidingWindowLimiter

    client = _client(engine, fake_embedder)
    # Shrink the limiter so the flood guard trips deterministically.
    client.app.state.feedback_rate_limiter = SlidingWindowLimiter(max_events=2, window_seconds=60.0)
    body = {"stage": "c1", "kind": "x"}
    assert client.post("/feedback", json=body, headers=_user_headers(10)).status_code == 200
    assert client.post("/feedback", json=body, headers=_user_headers(10)).status_code == 200
    # Third within the window is refused.
    assert client.post("/feedback", json=body, headers=_user_headers(10)).status_code == 429
    # A DIFFERENT actor is tracked independently (not penalised by 10's flood).
    assert client.post("/feedback", json=body, headers=_user_headers(11)).status_code == 200


def test_feedback_endpoint_rejects_an_unknown_stage(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    resp = client.post("/feedback", json={"stage": "c9", "kind": "x"}, headers=_user_headers(10))
    assert resp.status_code == 422
    assert _feedback_rows(engine) == []


def test_editing_the_draft_records_implicit_c7_feedback(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1, 2], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1, 2)),
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "fb1"})
    _events(client, "fb1")  # pause at send; C7 draft ready
    generated = client.get("/handoff/fb1").json()["draft"]

    edited = f"{generated}（追記: 10月中にお願いできると助かります）"
    saved = client.post("/handoff/draft", json={"session_id": "fb1", "draft": edited})
    assert saved.status_code == 200

    rows = _feedback_rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row.stage == "c7" and row.kind == "draft_edited"
    assert row.session_id == "fb1"
    assert row.payload["generated"] == generated and row.payload["sent"] == edited


def test_saving_the_draft_unchanged_records_no_feedback(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "fb2"})
    _events(client, "fb2")
    generated = client.get("/handoff/fb2").json()["draft"]

    # Re-saving the identical generated text is not a correction -> no feedback.
    saved = client.post("/handoff/draft", json={"session_id": "fb2", "draft": generated})
    assert saved.status_code == 200
    assert _feedback_rows(engine) == []


def test_dashboard_reports_feedback_by_stage(seed_counts, engine, fake_embedder) -> None:
    client = _client(engine, fake_embedder)
    for stage in ("c1", "c6", "c6"):
        client.post("/feedback", json={"stage": stage, "kind": "x"}, headers=_user_headers(10))
    data = client.get("/dashboard").json()
    assert data["feedback_by_stage"] == {"c1": 1, "c6": 2, "c7": 0, "total": 3}


def test_draft_feedback_write_failure_does_not_break_the_save(
    seed_counts, engine, fake_embedder, monkeypatch
) -> None:
    """A feedback-write failure must never surface as a 500: the draft edit is
    already committed by then (#237 review HIGH)."""
    import tekijin.api.service as svc

    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "fb3"})
    _events(client, "fb3")
    generated = client.get("/handoff/fb3").json()["draft"]

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("feedback backend is down")

    monkeypatch.setattr(svc, "record_feedback", _boom)

    edited = f"{generated}（追記: 期日を早めたいです）"
    saved = client.post("/handoff/draft", json={"session_id": "fb3", "draft": edited})
    # The save still succeeds despite the feedback write blowing up...
    assert saved.status_code == 200 and saved.json()["status"] == "draft_saved"
    # ...and the edit is durably persisted...
    assert client.get("/handoff/fb3").json()["draft"] == edited
    # ...while no feedback row was recorded (the write failed and was swallowed).
    assert _feedback_rows(engine) == []


def test_feedback_rejects_an_oversized_payload(seed_counts, engine, fake_embedder) -> None:
    """An authenticated caller must not be able to grow the JSONB column unbounded
    (storage-exhaustion DoS, #237 security review)."""
    client = _client(engine, fake_embedder)
    big = {"x": "あ" * 20000}  # ~60KB UTF-8, over the 16KB cap
    resp = client.post(
        "/feedback", json={"stage": "c1", "kind": "x", "payload": big}, headers=_user_headers(10)
    )
    assert resp.status_code == 422
    assert _feedback_rows(engine) == []


def test_feedback_with_unknown_question_id_records_without_the_link(
    seed_counts, engine, fake_embedder
) -> None:
    """An unknown question_id must not 500 (FK violation) nor leak existence — it is
    recorded without the dangling link (#237 security review)."""
    client = _client(engine, fake_embedder)
    resp = client.post(
        "/feedback",
        json={"stage": "c6", "kind": "x", "question_id": "api_does_not_exist"},
        headers=_user_headers(10),
    )
    assert resp.status_code == 200
    rows = _feedback_rows(engine)
    assert len(rows) == 1 and rows[0].question_id is None
