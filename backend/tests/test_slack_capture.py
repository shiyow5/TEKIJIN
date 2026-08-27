"""Solve-capture (#476 Screen 02): ✅ reaction on a hand-off thread → knowledge draft.

Unit tests drive the reaction dispatcher's gating with a fake ``schedule_solve_capture``
(no DB, no thread); integration tests seed a resolved hand-off and exercise the thread
assembler + the capture orchestration against the live database with a fake extractor.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from tekijin.api import slack_routes
from tekijin.config import Settings
from tekijin.data.db import get_sessionmaker, session_scope
from tekijin.data.knowledge import get_knowledge_unit_by_source
from tekijin.data.slack_channel_links import create_channel_link
from tekijin.data.slack_links import upsert_slack_link
from tekijin.knowledge.extract import CaseExtractor
from tekijin.knowledge.slack_thread import slack_thread_source
from tekijin.llm.schemas import CaseExtractionSchema
from tekijin.models.tables import Answer, Employee, Question, Recommendation
from tekijin.slack.capture import capture_resolved_thread

NOW = dt.datetime(2026, 8, 27, 12, 0, 0)  # noqa: DTZ001 - naive, matches created_at


# --------------------------------------------------------------------------- #
# reaction dispatcher gating (no DB, no network)
# --------------------------------------------------------------------------- #
def _capture_calls(monkeypatch, *, enabled: bool) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(slack_routes, "schedule_solve_capture", lambda _sf, **kw: calls.append(kw))
    monkeypatch.setattr(
        slack_routes, "get_settings", lambda: SimpleNamespace(slack_solve_capture_enabled=enabled)
    )
    return calls


def _reaction_event(reaction: str = "white_check_mark", **over) -> dict:
    event = {
        "type": "reaction_added",
        "reaction": reaction,
        "user": "U_REACTOR",
        "item": {"type": "message", "channel": "C_THREAD", "ts": "1.0"},
    }
    event.update(over)
    return event


def test_reaction_schedules_capture_when_enabled(monkeypatch) -> None:
    calls = _capture_calls(monkeypatch, enabled=True)
    slack_routes._handle_reaction_event(object(), _reaction_event())
    assert calls == [{"channel_id": "C_THREAD", "reactor_slack_user_id": "U_REACTOR"}]


def test_reaction_is_noop_when_flag_off(monkeypatch) -> None:
    calls = _capture_calls(monkeypatch, enabled=False)
    slack_routes._handle_reaction_event(object(), _reaction_event())
    assert calls == []


def test_reaction_ignores_non_solve_emoji(monkeypatch) -> None:
    calls = _capture_calls(monkeypatch, enabled=True)
    slack_routes._handle_reaction_event(object(), _reaction_event(reaction="tada"))
    assert calls == []


def test_reaction_ignores_other_event_types(monkeypatch) -> None:
    calls = _capture_calls(monkeypatch, enabled=True)
    slack_routes._handle_reaction_event(object(), _reaction_event(type="reaction_removed"))
    assert calls == []


def test_reaction_requires_channel_and_user(monkeypatch) -> None:
    calls = _capture_calls(monkeypatch, enabled=True)
    slack_routes._handle_reaction_event(object(), _reaction_event(item={"channel": ""}))
    slack_routes._handle_reaction_event(object(), _reaction_event(user=""))
    assert calls == []


# --------------------------------------------------------------------------- #
# thread assembler + capture orchestration (live DB)
# --------------------------------------------------------------------------- #
# Ids far outside any fixture range to avoid collisions in the shared DB.
ASKER, RESPONDER, BYSTANDER = 940_301, 940_302, 940_303
CHANNEL, TEAM = "C_SLKCAP", "T_SLKCAP"
TOPICS = ["ネットワーク・VPN"]


@pytest.fixture
def _resolved_thread(engine, seed_counts):
    """Seed one accepted hand-off with an answer + a pair-channel link; commit so a
    separate session (the capture's own) can read it. Returns (factory, thread_id)."""

    factory = get_sessionmaker(engine)
    with session_scope(factory) as session:
        session.add_all(
            [
                Employee(id=ASKER, name="質問者", email="asker@slkcap"),
                Employee(id=RESPONDER, name="回答者", email="responder@slkcap"),
                Employee(id=BYSTANDER, name="第三者", email="bystander@slkcap"),
            ]
        )
        session.add(
            Question(id="slkcap_q", asker_id=ASKER, body="VPNの拠点間接続が切れる", topics=TOPICS)
        )
        rec = Recommendation(
            question_id="slkcap_q", employee_id=RESPONDER, rank=1, outcome="accepted"
        )
        session.add(rec)
        session.flush()  # assign rec.id (the thread id)
        thread_id = rec.id
        session.add(
            Answer(
                id="slkcap_a",
                question_id="slkcap_q",
                responder_id=RESPONDER,
                body="MTUを1400に下げると安定します",
            )
        )
        create_channel_link(
            session,
            ASKER,
            RESPONDER,
            thread_id=thread_id,
            slack_channel_id=CHANNEL,
            slack_team_id=TEAM,
            now=NOW,
        )
        upsert_slack_link(session, RESPONDER, slack_user_id="U_RESP", slack_team_id=TEAM, now=NOW)
        upsert_slack_link(session, BYSTANDER, slack_user_id="U_BYSTD", slack_team_id=TEAM, now=NOW)
    yield factory, thread_id
    # Clean up so the committed rows do not leak into other tests on the shared DB.
    with session_scope(factory) as session:
        from tekijin.models.tables import KnowledgeUnit, SlackChannelLink, SlackLink

        session.query(KnowledgeUnit).filter(
            KnowledgeUnit.source_id == f"slack_thread_{thread_id}"
        ).delete()
        session.query(Answer).filter(Answer.id == "slkcap_a").delete()
        session.query(Recommendation).filter(Recommendation.question_id == "slkcap_q").delete()
        session.query(Question).filter(Question.id == "slkcap_q").delete()
        session.query(SlackChannelLink).filter(
            SlackChannelLink.slack_channel_id == CHANNEL
        ).delete()
        session.query(SlackLink).filter(SlackLink.employee_id.in_([RESPONDER, BYSTANDER])).delete()
        session.query(Employee).filter(Employee.id.in_([ASKER, RESPONDER, BYSTANDER])).delete()


def _extractor(*, extractable: bool = True) -> CaseExtractor:
    schema = CaseExtractionSchema(
        extractable=extractable,
        problem="VPNの拠点間接続が切れる" if extractable else "",
        action="MTUを1400に下げる" if extractable else "",
        result="安定した",
        confidence=0.8,
    )

    class _FakeModel:
        def invoke(self, _prompt):
            return schema

    return CaseExtractor(model=_FakeModel())


def _settings() -> Settings:
    return Settings(_env_file=None, slack_solve_capture_enabled=True)  # type: ignore[call-arg]


def test_slack_thread_source_assembles_question_and_answer(_resolved_thread) -> None:
    factory, thread_id = _resolved_thread
    with session_scope(factory) as session:
        source = slack_thread_source(session, thread_id)
    assert source is not None
    assert source.source_type == "slack_thread"
    assert source.source_id == f"slack_thread_{thread_id}"
    assert source.topics == tuple(TOPICS)  # provenance from the question's tags
    assert "VPNの拠点間接続が切れる" in source.text  # 質問
    assert "MTUを1400に下げると安定します" in source.text  # 回答 (captured answer body)


def test_slack_thread_source_none_for_unknown_thread(_resolved_thread) -> None:
    factory, _thread_id = _resolved_thread
    with session_scope(factory) as session:
        assert slack_thread_source(session, 9_999_999) is None


def test_capture_stores_unreviewed_draft(_resolved_thread) -> None:
    factory, thread_id = _resolved_thread
    stored = capture_resolved_thread(
        factory,
        channel_id=CHANNEL,
        reactor_slack_user_id="U_RESP",
        extractor=_extractor(),
        settings=_settings(),
    )
    assert stored == f"slack_thread_{thread_id}"
    with session_scope(factory) as session:
        unit = get_knowledge_unit_by_source(session, "slack_thread", f"slack_thread_{thread_id}")
    assert unit is not None
    assert unit.review_status == "unreviewed"  # lands in the draft box
    assert unit.kind == "case"
    assert unit.topics == tuple(TOPICS)


def test_capture_noop_when_flag_off(_resolved_thread) -> None:
    factory, thread_id = _resolved_thread
    disabled = Settings(_env_file=None, slack_solve_capture_enabled=False)  # type: ignore[call-arg]
    stored = capture_resolved_thread(
        factory,
        channel_id=CHANNEL,
        reactor_slack_user_id="U_RESP",
        extractor=_extractor(),
        settings=disabled,
    )
    assert stored is None
    with session_scope(factory) as session:
        assert (
            get_knowledge_unit_by_source(session, "slack_thread", f"slack_thread_{thread_id}")
            is None
        )


def test_capture_rejects_non_participant_reactor(_resolved_thread) -> None:
    factory, thread_id = _resolved_thread
    # U_BYSTD is a linked employee but not the asker/responder of this thread.
    stored = capture_resolved_thread(
        factory,
        channel_id=CHANNEL,
        reactor_slack_user_id="U_BYSTD",
        extractor=_extractor(),
        settings=_settings(),
    )
    assert stored is None
    with session_scope(factory) as session:
        assert (
            get_knowledge_unit_by_source(session, "slack_thread", f"slack_thread_{thread_id}")
            is None
        )


def test_capture_noop_for_unknown_channel(_resolved_thread) -> None:
    factory, _thread_id = _resolved_thread
    stored = capture_resolved_thread(
        factory,
        channel_id="C_NOT_OURS",
        reactor_slack_user_id="U_RESP",
        extractor=_extractor(),
        settings=_settings(),
    )
    assert stored is None


def test_capture_skips_when_model_declines(_resolved_thread) -> None:
    factory, thread_id = _resolved_thread
    stored = capture_resolved_thread(
        factory,
        channel_id=CHANNEL,
        reactor_slack_user_id="U_RESP",
        extractor=_extractor(extractable=False),
        settings=_settings(),
    )
    assert stored is None  # model said "not a case" -> nothing stored
    with session_scope(factory) as session:
        assert (
            get_knowledge_unit_by_source(session, "slack_thread", f"slack_thread_{thread_id}")
            is None
        )
