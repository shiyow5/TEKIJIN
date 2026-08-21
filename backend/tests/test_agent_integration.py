"""Integration tests for the LangGraph agent against live PostgreSQL + pgvector.

Uses the shared ``session`` / ``seed_counts`` / ``fake_embedder`` fixtures. The
LLM nodes are the deterministic stubs and the C4 retriever is usually an injected
fake returning a controlled retrieval dict, so every branch is exercised without
a model or retrieval nondeterminism — while C6 still scores against the real
seeded DB. ``now`` is injected, so runs are reproducible.
"""

from __future__ import annotations

import datetime as dt

from langgraph.types import Command

from tekijin.agent import build_agent
from tekijin.agent.route import DOCUMENT, PERSON, PRIOR_ANSWER
from tekijin.models.tables import Skill
from tekijin.retrieval.indexing import embed_corpus

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)
TOPIC = "ネットワーク・VPN"
# A well-formed question: yields topic ネットワーク・VPN and satisfies C2 (mentions a
# current product and 拠点), so no clarification interrupt on the happy path.
GOOD_Q = "現行のVPN機器で3拠点の拠点間接続について相談したいです"


class _FakeRetriever:
    """C4 stand-in returning a fixed retrieval dict (controls the C5 route)."""

    def __init__(self, *, answers=(), documents=(), people=()) -> None:
        self._payload = {
            "past_answers": list(answers),
            "documents": list(documents),
            "candidate_people": list(people),
        }

    def search(self, query: str) -> dict:
        return self._payload


def _seed_skill(session, sid: str, employee_id: int, topic: str = TOPIC) -> None:
    session.add(Skill(id=sid, employee_id=employee_id, topic=topic, level="中級", source="self"))
    session.flush()


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _init(question: str = GOOD_Q, asker=None) -> dict:
    return {
        "question": question,
        "asker": asker,
        "now": NOW,
        "followup_count": 0,
        "declined_ids": [],
    }


def _is_paused(agent, cfg) -> bool:
    return bool(agent.get_state(cfg).next)


# --------------------------------------------------------------------------- #
# happy path: person route -> recommendations -> hand-off
# --------------------------------------------------------------------------- #
def test_happy_path_person_route(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2, 3):
        _seed_skill(session, f"sk_happy_{emp}", emp)
    retriever = _FakeRetriever(
        answers=[{"qa_id": "a", "score": 0.005, "responder_id": 1}],
        documents=[{"doc_id": "d", "score": 0.001}],
        people=[1, 2, 3],
    )
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("happy")

    state = agent.invoke(_init(), cfg)
    assert state["route"] == PERSON
    assert state["recommendations"]  # C6 scored the candidates
    assert _is_paused(agent, cfg)  # paused at send for the outcome

    final = agent.invoke(Command(resume="accepted"), cfg)
    assert "取り次ぎました" in final["answer"]
    assert final["draft"] and final["recommendations"][0]["name"] in final["answer"]


# --------------------------------------------------------------------------- #
# out_of_scope terminal
# --------------------------------------------------------------------------- #
def test_out_of_scope_is_deflected(seed_counts, session, fake_embedder) -> None:
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever(people=[1]))
    state = agent.invoke(_init("今日の天気とランチのおすすめを教えて"), _cfg("oos"))
    assert state["out_of_scope"] is True
    assert "業務の範囲外" in state["answer"]
    assert "recommendations" not in state or not state.get("recommendations")


# --------------------------------------------------------------------------- #
# C2 insufficient -> interrupt -> resume -> proceeds
# --------------------------------------------------------------------------- #
def test_insufficient_triggers_interrupt_then_resumes(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2):
        _seed_skill(session, f"sk_insuf_{emp}", emp)
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever(people=[1, 2]))
    cfg = _cfg("insuf")

    # Topic-only question -> both slots missing -> ask interrupt.
    agent.invoke(_init("ネットワークの技術相談です"), cfg)
    assert _is_paused(agent, cfg)
    state_at_ask = agent.get_state(cfg)
    assert state_at_ask.next == ("ask",)

    # Provide the missing info; the followup cap makes C2 sufficient next pass.
    agent.invoke(Command(resume="現行はVPN機器で、対象は3拠点です"), cfg)
    after = agent.get_state(cfg).values
    assert after["followup_count"] == 1
    assert after["sufficient"] is True
    assert _is_paused(agent, cfg)  # now paused at send

    final = agent.invoke(Command(resume="accepted"), cfg)
    assert "取り次ぎました" in final["answer"]


# --------------------------------------------------------------------------- #
# C5 three routes
# --------------------------------------------------------------------------- #
def test_prior_answer_route(seed_counts, session, fake_embedder) -> None:
    _seed_skill(session, "sk_pa_1", 1)
    retriever = _FakeRetriever(
        answers=[{"qa_id": "a", "score": 0.05, "responder_id": 1}],  # clears threshold
        people=[1],
    )
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("pa")
    state = agent.invoke(_init(), cfg)
    assert state["route"] == PRIOR_ANSWER
    assert state["prior_answer_note"] and "回答" in state["prior_answer_note"]
    # prior_answer still flows to C6 (hand off to the responder).
    assert _is_paused(agent, cfg)  # reached send after C6/C7


def test_document_route_is_terminal(seed_counts, session, fake_embedder) -> None:
    retriever = _FakeRetriever(
        documents=[{"doc_id": "doc_0007", "score": 0.05}],
        people=[],  # weak person signal -> demotion
    )
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("doc")
    state = agent.invoke(_init(), cfg)
    assert state["route"] == DOCUMENT
    assert "doc_0007" in state["answer"]
    assert not _is_paused(agent, cfg)  # terminal, no interrupt


# --------------------------------------------------------------------------- #
# decline -> reroute to the next candidate
# --------------------------------------------------------------------------- #
def test_decline_reroutes_to_next_candidate(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2):
        _seed_skill(session, f"sk_dec_{emp}", emp)
    retriever = _FakeRetriever(people=[1, 2])
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("decline")

    first = agent.invoke(_init(), cfg)
    first_pick = first["recommendations"][0]["person_id"]

    # The first pick declines -> reroute excludes them and re-scores.
    agent.invoke(Command(resume="declined"), cfg)
    rerouted = agent.get_state(cfg).values
    assert first_pick in rerouted["declined_ids"]
    second_pick = rerouted["recommendations"][0]["person_id"]
    assert second_pick != first_pick

    final = agent.invoke(Command(resume="accepted"), cfg)
    assert "取り次ぎました" in final["answer"]


def test_no_candidate_terminal(seed_counts, session, fake_embedder) -> None:
    # Nothing retrieved at all -> person fallback with no candidates -> the
    # no_candidate terminal (C6 has nobody to score).
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever())
    state = agent.invoke(_init(), _cfg("nocand"))
    assert state["route"] == PERSON
    assert state["recommendations"] == []
    assert "適任者が見つかりません" in state["answer"]


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_run_is_deterministic(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2, 3):
        _seed_skill(session, f"sk_det_{emp}", emp)
    retriever = _FakeRetriever(people=[1, 2, 3])

    def run(thread: str) -> dict:
        agent = build_agent(fake_embedder, session, retriever=retriever)
        cfg = _cfg(thread)
        agent.invoke(_init(), cfg)
        return agent.invoke(Command(resume="accepted"), cfg)

    first = run("det_a")
    second = run("det_b")
    assert first["answer"] == second["answer"]
    assert [r["person_id"] for r in first["recommendations"]] == [
        r["person_id"] for r in second["recommendations"]
    ]
    assert first["route"] == second["route"]


# --------------------------------------------------------------------------- #
# stream updates (the basis for #32 SSE)
# --------------------------------------------------------------------------- #
def test_stream_yields_node_updates(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2):
        _seed_skill(session, f"sk_stream_{emp}", emp)
    retriever = _FakeRetriever(people=[1, 2])
    agent = build_agent(fake_embedder, session, retriever=retriever)

    nodes = []
    for update in agent.stream(_init(), _cfg("stream"), stream_mode="updates"):
        nodes.extend(update.keys())
    # The deterministic node sequence up to the send interrupt.
    assert nodes[:6] == [
        "c1_intent",
        "c2_sufficiency",
        "c3_embed",
        "c4_retrieve",
        "c5_route",
        "c6_score",
    ]
    assert "c7_draft" in nodes


# --------------------------------------------------------------------------- #
# real HybridRetriever wiring (smoke: no assertion on which people)
# --------------------------------------------------------------------------- #
def test_real_retriever_end_to_end(seed_counts, session, fake_embedder) -> None:
    embed_corpus(session, fake_embedder)  # populate dense vectors for real C4
    agent = build_agent(fake_embedder, session)  # default HybridRetriever
    cfg = _cfg("real")
    state = agent.invoke(_init(), cfg)
    # The real retrieval drove a valid route and the run did not error.
    assert state["route"] in {PERSON, PRIOR_ANSWER, DOCUMENT}
    assert isinstance(state["retrieval"]["candidate_people"], list)
