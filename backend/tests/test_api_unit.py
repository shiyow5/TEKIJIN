"""Database-free unit tests for the API layer.

Covers request/SSE Pydantic contracts, the node->event mapping, the checkpointer
factory (memory + postgres fallback), the LLM-backend factory, and the vLLM
adapters (with an injected fake model — no network, no langchain-openai import).
"""

from __future__ import annotations

import json

import pytest
from sse_starlette import ServerSentEvent

from tekijin.agent.protocols import IntentResult
from tekijin.agent.stubs import KeywordIntentModel
from tekijin.api import events, schemas
from tekijin.config import Settings
from tekijin.llm.factory import make_llm_nodes
from tekijin.llm.schemas import IntentSchema, SufficiencySchema
from tekijin.llm.vllm import VllmDraftModel, VllmIntentModel, VllmSufficiencyModel


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# request / resume schemas
# --------------------------------------------------------------------------- #
def test_ask_request_requires_nonempty_fields() -> None:
    ok = schemas.AskRequest(asker_id=1, question="q", session_id="s")
    assert ok.asker_id == 1
    with pytest.raises(ValueError):
        schemas.AskRequest(asker_id=1, question="", session_id="s")
    with pytest.raises(ValueError):
        schemas.AskRequest(asker_id=1, question="q", session_id="")


def test_resume_request_exactly_one_of_outcome_or_reply() -> None:
    assert schemas.ResumeRequest(session_id="s", outcome="accepted").resume_value == "accepted"
    assert schemas.ResumeRequest(session_id="s", reply="現行はVPN").resume_value == "現行はVPN"
    with pytest.raises(ValueError):
        schemas.ResumeRequest(session_id="s")  # neither
    with pytest.raises(ValueError):
        schemas.ResumeRequest(session_id="s", outcome="accepted", reply="x")  # both
    with pytest.raises(ValueError):
        schemas.ResumeRequest(session_id="s", reply="   ")  # empty reply
    with pytest.raises(ValueError):
        schemas.ResumeRequest(session_id="s", outcome="maybe")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# node -> SSE event mapping
# --------------------------------------------------------------------------- #
def _ev(sse: ServerSentEvent | None) -> ServerSentEvent:
    assert sse is not None
    return sse


def _data(sse: ServerSentEvent | None) -> dict:
    data = _ev(sse).data
    assert data is not None
    return json.loads(data)


def test_node_event_understood() -> None:
    sse = events.node_event(
        "c1_intent",
        {
            "topics": ["セキュリティ"],
            "products": ["UTM"],
            "question_type": "技術相談",
            "intent_confidence": 0.9,
        },
    )
    assert sse is not None and sse.event == "understood"
    assert _data(sse) == {
        "topics": ["セキュリティ"],
        "products": ["UTM"],
        "situation": None,
        "question_type": "技術相談",
        "confidence": 0.9,
    }


def test_node_event_route_recommend_draft_done() -> None:
    route = _ev(
        events.node_event(
            "c5_route", {"route": "person", "route_reason": "r", "route_confidence": 0.3}
        )
    )
    assert route.event == "route" and _data(route)["route"] == "person"

    recommend = _ev(
        events.node_event(
            "c6_score",
            {
                "recommendations": [
                    {"person_id": 1, "name": "E1", "dept": "d", "score": 0.5, "confidence": "中"}
                ]
            },
        )
    )
    assert recommend.event == "recommend"
    assert _data(recommend)["recommendations"][0]["person_id"] == 1

    draft = _ev(events.node_event("c7_draft", {"draft": "文面"}))
    assert draft.event == "draft" and _data(draft)["draft"] == "文面"

    done = _ev(events.node_event("c8_update", {"answer": "取り次ぎました"}))
    assert done.event == "done" and _data(done) == {"status": "sent", "answer": "取り次ぎました"}


def test_node_event_terminals_are_messages() -> None:
    for node, status in [
        ("off_topic", "off_topic"),
        ("document", "document"),
        ("unresolved_intent", "unresolved"),
        ("no_candidate", "no_candidate"),
    ]:
        sse = _ev(events.node_event(node, {"answer": "終端メッセージ"}))
        assert sse.event == "message"
        assert _data(sse) == {"status": status, "message": "終端メッセージ"}


def test_node_event_internal_nodes_emit_nothing() -> None:
    for node in ("reset", "c2_sufficiency", "c3_embed", "c4_retrieve", "prior_answer", "reroute"):
        assert events.node_event(node, {"x": 1}) is None
    assert (
        frozenset(
            {
                "c1_intent",
                "c5_route",
                "c6_score",
                "c7_draft",
                "c8_update",
                "off_topic",
                "document",
                "unresolved_intent",
                "no_candidate",
            }
        )
        == events.EVENT_NODES
    )


def test_interrupt_event_followup_and_send() -> None:
    followup = _ev(
        events.interrupt_event({"followup_question": "現行製品は?", "missing": ["現行製品"]})
    )
    assert followup.event == "followup"
    assert _data(followup) == {"question": "現行製品は?", "missing": ["現行製品"]}
    # A send interrupt (draft/responder payload) emits nothing.
    assert events.interrupt_event({"draft": "d", "responder": {"person_id": 1}}) is None
    assert events.interrupt_event({}) is None


# --------------------------------------------------------------------------- #
# checkpointer factory
# --------------------------------------------------------------------------- #
def test_make_checkpointer_memory() -> None:
    from langgraph.checkpoint.memory import MemorySaver

    from tekijin.api.checkpointer import make_checkpointer

    assert isinstance(make_checkpointer(_settings(checkpointer_backend="memory")), MemorySaver)


def test_make_checkpointer_postgres_falls_back(monkeypatch) -> None:
    from langgraph.checkpoint.memory import MemorySaver

    import tekijin.api.checkpointer as ck

    def _boom(_url: str):
        raise RuntimeError("no database")

    monkeypatch.setattr(ck, "make_postgres_checkpointer", _boom)
    cp = ck.make_checkpointer(_settings(checkpointer_backend="postgres"))
    assert isinstance(cp, MemorySaver)  # fell back


def test_postgres_conn_string_strips_driver() -> None:
    from tekijin.api.checkpointer import _postgres_conn_string

    assert (
        _postgres_conn_string("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    )


# --------------------------------------------------------------------------- #
# LLM backend factory
# --------------------------------------------------------------------------- #
def test_make_llm_nodes_stub() -> None:
    intent, sufficiency, draft = make_llm_nodes(_settings(llm_backend="stub"))
    assert isinstance(intent, KeywordIntentModel)


def test_make_llm_nodes_vllm_constructs_without_network() -> None:
    intent, sufficiency, draft = make_llm_nodes(_settings(llm_backend="vllm"))
    assert isinstance(intent, VllmIntentModel)
    assert isinstance(sufficiency, VllmSufficiencyModel)
    assert isinstance(draft, VllmDraftModel)


# --------------------------------------------------------------------------- #
# vLLM adapters (injected fake model)
# --------------------------------------------------------------------------- #
class _FakeStructured:
    def __init__(self, result) -> None:
        self._result = result

    def invoke(self, _prompt):
        return self._result


def test_vllm_intent_adapter_converts_schema() -> None:
    model = _FakeStructured(
        IntentSchema(
            topics=["セキュリティ"], products=["UTM"], question_type="技術相談", confidence=0.8
        )
    )
    result = VllmIntentModel(model=model).analyze("UTM移行の相談", {"id": 1})
    assert isinstance(result, IntentResult)
    assert result.topics == ["セキュリティ"] and result.products == ["UTM"]
    assert result.question_type == "技術相談" and result.confidence == 0.8
    # prompt() is pure and mentions the question.
    assert any(
        "UTM移行の相談" in msg for _role, msg in VllmIntentModel.prompt("UTM移行の相談", None)
    )


def test_vllm_sufficiency_adapter_converts_schema() -> None:
    model = _FakeStructured(
        SufficiencySchema(sufficient=False, missing=["現行製品"], followup_question="製品は?")
    )
    intent = IntentResult(topics=["セキュリティ"], question_type="技術相談")
    result = VllmSufficiencyModel(model=model).check("q", intent, 0)
    assert result.sufficient is False and result.missing == ["現行製品"]
    assert result.followup_question == "製品は?"


# --------------------------------------------------------------------------- #
# service helpers
# --------------------------------------------------------------------------- #
def test_default_now_is_naive() -> None:
    import datetime as dt

    from tekijin.api.service import _default_now

    now = _default_now()
    assert isinstance(now, dt.datetime)
    assert now.tzinfo is None  # naive, as the scorer requires


def test_interrupt_payload_extracts_value_and_defaults() -> None:
    from tekijin.api.service import _interrupt_payload

    class _Interrupt:
        value = {"followup_question": "?"}

    assert _interrupt_payload((_Interrupt(),)) == {"followup_question": "?"}
    assert _interrupt_payload(None) == {}  # non-tuple / falsy -> empty payload
    assert _interrupt_payload(()) == {}


def test_vllm_draft_adapter_reads_content_or_str() -> None:
    class _Msg:
        content = "高梨さん、ご相談です。"

    class _ChatObj:
        def invoke(self, _prompt):
            return _Msg()

    class _ChatStr:
        def invoke(self, _prompt):
            return "プレーンテキスト下書き"

    assert VllmDraftModel(model=_ChatObj()).draft("q", {"name": "高梨"}, None, ["現行製品"]) == (
        "高梨さん、ご相談です。"
    )
    assert VllmDraftModel(model=_ChatStr()).draft("q", {"name": "高梨"}, None, []) == (
        "プレーンテキスト下書き"
    )
