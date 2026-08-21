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
from tekijin.api.service import AgentService
from tekijin.app import create_app
from tekijin.data.dashboard import dashboard_summary
from tekijin.data.db import get_sessionmaker
from tekijin.models.tables import Recommendation

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
    assert [r["person_id"] for r in evs[2][1]["recommendations"]] == [1, 2, 3]
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
    assert first[2][1]["recommendations"][0]["person_id"] == 1  # drafted for 1

    # Decline -> reroute -> re-scored excluding 1 -> next candidate 2.
    client.post("/answer", json={"session_id": "s3", "outcome": "declined"})
    second = _events(client, "s3")
    assert [e for e, _ in second] == ["recommend", "draft"]
    assert second[0][1]["recommendations"][0]["person_id"] == 2

    client.post("/answer", json={"session_id": "s3", "outcome": "accepted"})
    done = _events(client, "s3")
    assert done[0][0] == "done"


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
    assert body["recent_recommendations"] == []


def test_dashboard_summary_includes_recent_recommendations(seed_counts, session) -> None:
    # Direct data-layer test: flushed rows are visible within the same session.
    session.add(
        Recommendation(question_id="q_0001", employee_id=3, rank=1, score=0.87, outcome="accepted")
    )
    session.flush()
    summary = dashboard_summary(session)
    assert summary["recommendation_count"] >= 1
    top = summary["recent_recommendations"][0]
    assert top["employee_id"] == 3 and top["name"] and top["score"] == 0.87


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
def test_postgres_checkpointer_persists(seed_counts, engine, fake_embedder, database_url) -> None:
    from tekijin.api.checkpointer import make_postgres_checkpointer

    try:
        checkpointer = make_postgres_checkpointer(database_url)
    except Exception as exc:  # pragma: no cover - environment without a usable DB
        pytest.skip(f"PostgresSaver unavailable: {exc}")

    client = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1)),
        checkpointer=checkpointer,
    )
    client.post("/ask", json={"asker_id": 10, "question": GOOD_Q, "session_id": "pg1"})
    assert [e for e, _ in _events(client, "pg1")] == ["understood", "route", "recommend", "draft"]
    # A brand-new client (fresh graph) sharing the SAME postgres checkpointer resumes.
    client2 = _client(
        engine,
        fake_embedder,
        retriever=_FakeRetriever(people=[1], people_confidence=0.2),
        scorer=_FakeScorer(_recs(1)),
        checkpointer=checkpointer,
    )
    client2.post("/answer", json={"session_id": "pg1", "outcome": "accepted"})
    done = _events(client2, "pg1")
    assert done[0][0] == "done"
