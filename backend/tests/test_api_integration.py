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
    _SessionCtx,
)
from tekijin.app import create_app
from tekijin.data.dashboard import dashboard_summary
from tekijin.data.db import get_sessionmaker
from tekijin.models.tables import Answer, Question, Recommendation

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)
GOOD_Q = "現行のVPN機器で3拠点の拠点間接続について相談したいです"


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


def _client(engine, embedder, *, retriever=None, scorer=None, checkpointer=None) -> TestClient:
    service = AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=checkpointer or MemorySaver(),
        embedder=embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=TemplateDraftModel(),
        retriever=retriever,
        scorer=scorer,
        now_factory=lambda: NOW,
    )
    return TestClient(create_app(agent_service=service))


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
        session.execute(
            text(r"DELETE FROM recommendations WHERE question_id LIKE 'api\_%' ESCAPE '\'")
        )
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
    assert done == [("done", {"status": "sent", "answer": done[0][1]["answer"]})]
    assert "取り次ぎました" in done[0][1]["answer"]


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
    client.post(
        "/ask", json={"asker_id": 10, "question": "ネットワークの技術相談です", "session_id": "s2"}
    )
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
    client.post(
        "/ask", json={"asker_id": 10, "question": "ネットワークの技術相談です", "session_id": "c4"}
    )
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
    client.post(
        "/ask", json={"asker_id": 10, "question": "ネットワークの技術相談です", "session_id": "r2"}
    )
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
    with TestClient(create_app(agent_service=service)) as client:
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


def test_handoff_conflicts_when_awaiting_clarification(seed_counts, engine, fake_embedder) -> None:
    client = _client(
        engine, fake_embedder, retriever=_FakeRetriever(people=[1]), scorer=_FakeScorer(_recs(1))
    )
    # Topic-only question -> paused at ``ask`` (a followup is owed to the asker).
    client.post(
        "/ask", json={"asker_id": 10, "question": "ネットワークの技術相談です", "session_id": "h2"}
    )
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
    client = TestClient(create_app(agent_service=service))
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
def _svc(engine, embedder, *, retriever=None, scorer=None) -> AgentService:
    return AgentService(
        session_factory=get_sessionmaker(engine),
        checkpointer=MemorySaver(),
        embedder=embedder,
        intent_model=KeywordIntentModel(),
        sufficiency_model=RuleSufficiencyModel(),
        draft_model=TemplateDraftModel(),
        retriever=retriever,
        scorer=scorer,
        now_factory=lambda: NOW,
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
    client = TestClient(create_app(agent_service=service))
    resp = client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "b"})
    assert resp.status_code == 500
    assert "内部エラー" in resp.text
    assert "secret internal" not in resp.text  # detail logged, never leaked


def test_answer_unexpected_error_is_generic_500(seed_counts, engine, fake_embedder) -> None:
    service = _svc(engine, fake_embedder, retriever=_FakeRetriever(), scorer=_FakeScorer([]))

    def _boom(*_a, **_k):
        raise RuntimeError("secret internal detail at 10.0.0.1")

    service.submit_resume = _boom  # type: ignore[method-assign]
    client = TestClient(create_app(agent_service=service))
    resp = client.post("/answer", json={"session_id": "b", "outcome": "accepted"})
    assert resp.status_code == 500
    assert "secret internal" not in resp.text


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
