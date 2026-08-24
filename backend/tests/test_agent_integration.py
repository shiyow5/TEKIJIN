"""Integration tests for the LangGraph agent against live PostgreSQL + pgvector.

Uses the shared ``session`` / ``seed_counts`` / ``fake_embedder`` fixtures. The
LLM nodes are the deterministic stubs and the C4 retriever is usually an injected
fake returning a controlled retrieval dict, so every branch is exercised without
a model or retrieval nondeterminism — while C6 still scores against the real
seeded DB. ``now`` is injected, so runs are reproducible.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from langgraph.types import Command

from tekijin.agent import build_agent
from tekijin.agent.route import DOCUMENT, PERSON, PRIOR_ANSWER
from tekijin.agent.state import RetrievalResult
from tekijin.models.tables import Skill
from tekijin.retrieval.indexing import embed_corpus

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)
TOPIC = "ネットワーク・VPN"
# A well-formed question: yields topic ネットワーク・VPN and satisfies C2 (mentions a
# current product and 拠点), so no clarification interrupt on the happy path.
GOOD_Q = "現行のVPN機器で3拠点の拠点間接続について相談したいです"


class _FakeRetriever:
    """C4 stand-in returning a fixed retrieval dict (controls the C5 route)."""

    def __init__(
        self,
        *,
        answers=(),
        documents=(),
        people=(),
        answer_confidence=0.0,
        document_confidence=0.0,
        people_confidence=0.0,
    ) -> None:
        self._payload: RetrievalResult = {
            "past_answers": list(answers),
            "documents": list(documents),
            "candidate_people": list(people),
            "answer_confidence": answer_confidence,
            "document_confidence": document_confidence,
            "people_confidence": people_confidence,
        }
        self.calls: list[tuple[str, bool]] = []  # (query, got_query_vector)

    def search(self, query: str, *, query_vector: Sequence[float] | None = None) -> RetrievalResult:
        self.calls.append((query, query_vector is not None))
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
        answers=[{"qa_id": "a", "score": 0.05, "responder_id": 1}],
        answer_confidence=0.9,  # near-duplicate past QA (absolute cosine)
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
        document_confidence=0.8,  # strongly on-topic document
        people_confidence=0.2,  # weak person signal
        people=[1, 2, 3],  # candidates present, but weak -> document still wins
    )
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("doc")
    state = agent.invoke(_init(), cfg)
    assert state["route"] == DOCUMENT
    assert "doc_0007" in state["answer"]
    assert state["document_id"] == "doc_0007"  # surfaced for the viewer (#143)
    assert not _is_paused(agent, cfg)  # terminal, no interrupt


# --------------------------------------------------------------------------- #
# decline -> reroute to the next candidate
# --------------------------------------------------------------------------- #
def test_decline_reroutes_to_next_candidate(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2, 3):
        _seed_skill(session, f"sk_dec_{emp}", emp)
    retriever = _FakeRetriever(people=[1, 2, 3])
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("decline")

    first = agent.invoke(_init(), cfg)
    first_recs = first["recommendations"]
    first_pick = first_recs[0]["person_id"]
    surviving = first_recs[1:]  # ranks 2/3 as shown before the decline

    # The first pick declines -> reroute keeps ranks 2/3 exactly as shown,
    # rather than rescoring everyone (#D5/#206). With only 3 people in the pool
    # total, there is nobody left to backfill the freed slot with, so the kept
    # 2 survivors are the whole result (the 4-candidate test below covers the
    # backfill case).
    agent.invoke(Command(resume="declined"), cfg)
    rerouted = agent.get_state(cfg).values
    assert first_pick in rerouted["declined_ids"]
    new_recs = rerouted["recommendations"]
    assert new_recs == surviving  # byte-identical survivors, nothing else
    second_pick = new_recs[0]["person_id"]
    assert second_pick != first_pick

    final = agent.invoke(Command(resume="accepted"), cfg)
    assert "取り次ぎました" in final["answer"]


def test_decline_reroute_backfills_only_freed_slot(seed_counts, session, fake_embedder) -> None:
    # 4 eligible candidates so a genuinely fresh 4th person is available to
    # backfill the slot freed by the decline, proving c6_score does not
    # rescore ranks 2/3 on reroute (#D5/#206).
    for emp in (1, 2, 3, 4):
        _seed_skill(session, f"sk_dec4_{emp}", emp)
    retriever = _FakeRetriever(people=[1, 2, 3, 4])
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("decline4")

    first = agent.invoke(_init(), cfg)
    first_recs = first["recommendations"]
    assert len(first_recs) == 3
    former_second, former_third = first_recs[1], first_recs[2]

    agent.invoke(Command(resume="declined"), cfg)
    rerouted = agent.get_state(cfg).values["recommendations"]
    assert len(rerouted) == 3
    assert rerouted[0] == former_second
    assert rerouted[1] == former_third
    shown_before = {r["person_id"] for r in first_recs}
    assert rerouted[2]["person_id"] not in shown_before  # fresh backfill


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
    # The deterministic node sequence up to the send interrupt (reset runs first).
    assert nodes[:7] == [
        "reset",
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
    retrieval = state["retrieval"]
    assert isinstance(retrieval["candidate_people"], list)
    # C4 emits the absolute cosine confidences C5 routes on, in [0, 1].
    for key in ("answer_confidence", "document_confidence", "people_confidence"):
        assert 0.0 <= retrieval[key] <= 1.0


def test_newly_covered_topic_reaches_recommendation(seed_counts, session, fake_embedder) -> None:
    # Fix 2: an EC question (previously uncoverable by the stub) now extracts the
    # ECサイト構築 topic, so a candidate with that skill can be recommended.
    _seed_skill(session, "sk_ec_1", 1, topic="ECサイト構築")
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever(people=[1]))
    q = "ECサイト構築について、現行のECサイトで3拠点向けに相談したいです"
    state = agent.invoke(_init(q), _cfg("ec"))
    assert "ECサイト構築" in state["topics"]
    assert [r["person_id"] for r in state["recommendations"]] == [1]


def test_c4_reuses_c3_query_vector(seed_counts, session, fake_embedder) -> None:
    # Fix 4: the C3 embedding is passed to C4.search so the dense channels do not
    # re-embed (one encode per query, not two).
    retriever = _FakeRetriever(people=[1])
    _seed_skill(session, "sk_reuse", 1)
    agent = build_agent(fake_embedder, session, retriever=retriever)
    agent.invoke(_init(), _cfg("reuse"))
    assert retriever.calls  # C4 was called
    assert all(got_vector for _, got_vector in retriever.calls)  # always with a vector


# --------------------------------------------------------------------------- #
# fix B: an unexpected outcome never reaches the success terminal
# --------------------------------------------------------------------------- #
def test_unexpected_outcome_does_not_reach_c8(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2):
        _seed_skill(session, f"sk_bad_{emp}", emp)
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever(people=[1, 2]))
    cfg = _cfg("badout")

    agent.invoke(_init(), cfg)
    assert agent.get_state(cfg).next == ("send",)
    # A garbage outcome loops back to send (re-confirm) — never c8_update.
    agent.invoke(Command(resume="maybe?"), cfg)
    assert agent.get_state(cfg).next == ("send",)
    assert agent.get_state(cfg).values.get("answer") is None
    # A valid outcome then completes.
    final = agent.invoke(Command(resume="accepted"), cfg)
    assert "取り次ぎました" in final["answer"]


# --------------------------------------------------------------------------- #
# fix D: a 2nd question on the same thread resets per-question control
# --------------------------------------------------------------------------- #
def test_second_question_resets_control_fields(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2):
        _seed_skill(session, f"sk_reset_{emp}", emp)
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever(people=[1, 2]))
    cfg = _cfg("twoq")  # SAME thread for both questions

    # Q1: decline the first pick so declined_ids gets populated.
    q1 = agent.invoke(_init(), cfg)
    first_pick = q1["recommendations"][0]["person_id"]
    agent.invoke(Command(resume="declined"), cfg)
    agent.invoke(Command(resume="accepted"), cfg)  # Q1 ends
    assert first_pick in agent.get_state(cfg).values["declined_ids"]

    # Q2: a brand-new question on the SAME thread -> reset clears the carry-over.
    agent.invoke(_init(), cfg)
    q2_state = agent.get_state(cfg).values
    assert q2_state["declined_ids"] == []  # not inherited from Q1
    assert q2_state["followup_count"] == 0
    # The person declined in Q1 is eligible again in Q2.
    assert first_pick in [r["person_id"] for r in q2_state["recommendations"]]


def test_resume_does_not_reset_followup_loop(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2):
        _seed_skill(session, f"sk_noreset_{emp}", emp)
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever(people=[1, 2]))
    cfg = _cfg("noreset")
    agent.invoke(_init("ネットワークの技術相談です"), cfg)  # insufficient -> ask
    assert agent.get_state(cfg).next == ("ask",)
    # Resume (NOT a new question) must keep the followup_count it accrued.
    agent.invoke(Command(resume="現行はVPN、3拠点です"), cfg)
    assert agent.get_state(cfg).values["followup_count"] == 1


# --------------------------------------------------------------------------- #
# fix E: the follow-up cap is enforced by the node, not just the stub
# --------------------------------------------------------------------------- #
def test_node_enforces_followup_cap(seed_counts, session, fake_embedder) -> None:
    from tekijin.agent.protocols import SufficiencyResult

    class _NeverSufficient:
        def check(self, question, intent, followup_count):  # ignores the cap
            return SufficiencyResult(sufficient=False, missing=["x"], followup_question="もっと？")

    for emp in (1, 2):
        _seed_skill(session, f"sk_cap_{emp}", emp)
    agent = build_agent(
        fake_embedder,
        session,
        sufficiency_model=_NeverSufficient(),
        retriever=_FakeRetriever(people=[1, 2]),
    )
    cfg = _cfg("cap")
    agent.invoke(_init(), cfg)  # model says insufficient -> ask
    assert agent.get_state(cfg).next == ("ask",)
    # After one clarification the NODE forces sufficiency -> proceeds (no loop).
    agent.invoke(Command(resume="追加情報です"), cfg)
    assert agent.get_state(cfg).next == ("send",)  # reached the hand-off, not ask


# --------------------------------------------------------------------------- #
# fix G: prior_answer hands off to the past responder, not a higher scorer
# --------------------------------------------------------------------------- #
def test_prior_answer_pins_the_responder(seed_counts, session, fake_embedder) -> None:
    # Retrieval lists candidate_people [1, 2, 3] but the strong past answer is by
    # employee 5; prior_answer must hand off to 5, not to a higher-scoring 1/2/3.
    for emp in (1, 2, 3):
        _seed_skill(session, f"sk_pin_{emp}", emp)  # strong candidates
    retriever = _FakeRetriever(
        answers=[{"qa_id": "a", "score": 0.05, "responder_id": 5}],
        answer_confidence=0.9,  # near-duplicate past QA
        people=[1, 2, 3],
    )
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("pin")
    state = agent.invoke(_init(), cfg)
    assert state["route"] == PRIOR_ANSWER
    assert state["pinned_responder_id"] == 5
    assert [r["person_id"] for r in state["recommendations"]] == [5]  # pinned


def test_prior_answer_falls_back_when_pinned_declines(seed_counts, session, fake_embedder) -> None:
    # Fix 3: the pinned past responder declines -> drop the pin and fall back to
    # the candidate_people pool (never dead-end on a single decline).
    for emp in (1, 2):
        _seed_skill(session, f"sk_pinfb_{emp}", emp)
    retriever = _FakeRetriever(
        answers=[{"qa_id": "a", "score": 0.05, "responder_id": 1}],
        answer_confidence=0.9,
        people=[1, 2],  # fallback pool
    )
    agent = build_agent(fake_embedder, session, retriever=retriever)
    cfg = _cfg("pinfb")
    state = agent.invoke(_init(), cfg)
    assert state["route"] == PRIOR_ANSWER
    assert [r["person_id"] for r in state["recommendations"]] == [1]  # pinned

    # Pinned responder 1 declines -> un-pin, recommend the next candidate (2).
    agent.invoke(Command(resume="declined"), cfg)
    rerouted = agent.get_state(cfg).values
    assert 1 in rerouted["declined_ids"]
    assert [r["person_id"] for r in rerouted["recommendations"]] == [2]
    final = agent.invoke(Command(resume="accepted"), cfg)
    assert "取り次ぎました" in final["answer"]


def test_prior_answer_pin_that_is_the_asker_falls_back(seed_counts, session, fake_embedder) -> None:
    # Fix 3: the top past answer's responder IS the asker -> the pin is invalid
    # (they cannot answer their own question); fall back to candidate_people.
    for emp in (1, 2):
        _seed_skill(session, f"sk_pinself_{emp}", emp)
    retriever = _FakeRetriever(
        answers=[{"qa_id": "a", "score": 0.05, "responder_id": 5}],  # responder == asker
        answer_confidence=0.9,
        people=[1, 2],  # fallback pool
    )
    agent = build_agent(fake_embedder, session, retriever=retriever)
    state = agent.invoke(_init(asker={"id": 5}), _cfg("pinself"))
    assert state["route"] == PRIOR_ANSWER
    assert state["pinned_responder_id"] == 5
    person_ids = [r["person_id"] for r in state["recommendations"]]
    assert 5 not in person_ids  # asker never recommended to themselves
    assert set(person_ids) <= {1, 2}


# --------------------------------------------------------------------------- #
# fix 5: unresolved intent (still no topic after one clarification) terminal
# --------------------------------------------------------------------------- #
def test_unresolved_intent_terminal(seed_counts, session, fake_embedder) -> None:
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever(people=[1]))
    cfg = _cfg("unresolved")
    # No topic extractable -> C2 asks to clarify (interrupt at ask).
    agent.invoke(_init("これについて教えて"), cfg)
    assert agent.get_state(cfg).next == ("ask",)
    # The reply STILL carries no topic -> capped + unresolved intent -> graceful
    # terminal, NOT a silent no_candidate.
    final = agent.invoke(Command(resume="よくわからないのですが"), cfg)
    assert not agent.get_state(cfg).next  # terminated
    assert final["intent_unresolved"] is True
    assert "特定できませんでした" in final["answer"]
    assert not final.get("recommendations")


# --------------------------------------------------------------------------- #
# fix I: a non-string resume is not interpolated into the question
# --------------------------------------------------------------------------- #
def test_ask_ignores_non_string_resume(seed_counts, session, fake_embedder) -> None:
    for emp in (1, 2):
        _seed_skill(session, f"sk_badreply_{emp}", emp)
    agent = build_agent(fake_embedder, session, retriever=_FakeRetriever(people=[1, 2]))
    cfg = _cfg("badreply")
    original = "ネットワークの技術相談です"
    agent.invoke(_init(original), cfg)
    assert agent.get_state(cfg).next == ("ask",)
    # A non-string resume payload is safely ignored (not appended); the cap then
    # lets the run proceed rather than corrupting the question.
    agent.invoke(Command(resume={"unexpected": "payload"}), cfg)
    values = agent.get_state(cfg).values
    assert values["question"] == original  # not corrupted with the dict
    assert agent.get_state(cfg).next == ("send",)
