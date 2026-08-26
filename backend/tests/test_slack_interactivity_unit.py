"""Unit tests for the Slack interactivity payload handler
(POST /slack/interactivity's 承諾/辞退/自分より適任がいる buttons).

Exercises ``_handle_interactivity_action`` / ``_advance_after_resume``
directly against a fake ``AgentService`` and a monkeypatched Slack-link
lookup — no real Slack signature, no real DB, no real LangGraph run.
Standing up a genuine session paused at "send" plus a signed Slack payload
would need the full DB integration harness that ``test_slack_integration.py``
already can't run locally (pgserver permission constraints, per PR #399), so
these tests pin down the handler's own logic instead: which employee is
allowed to act, that the outcome reaches ``submit_resume``, that the queued
resume gets drained afterwards, and that every failure path returns a
friendly 200 rather than letting an exception turn into the raw non-2xx that
makes Slack show its "processing failed" warning triangle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from tekijin.api import slack_routes
from tekijin.api.slack_routes import _advance_after_resume, _handle_interactivity_action


@dataclass
class _FakeLink:
    employee_id: int


class _NullSessionCtx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


@dataclass
class _FakeService:
    responder_id: int | None
    current_responder_id: int | None
    is_streamable_result: bool = True
    submitted: list[dict] = field(default_factory=list)
    streamed: bool = False

    def session_factory(self):
        return _NullSessionCtx()

    def session_participants(self, session_id):
        return (None, self.current_responder_id)

    def submit_resume(self, session_id, *, outcome, recommendation_id):
        self.submitted.append(
            {"session_id": session_id, "outcome": outcome, "recommendation_id": recommendation_id}
        )

    def is_streamable(self, session_id):
        return self.is_streamable_result

    def stream_events(self, session_id):
        self.streamed = True
        return iter(())


class _ImmediateThread:
    """Runs the target synchronously instead of on a real background thread,
    so a test can observe its effect without a sleep/join."""

    def __init__(self, target, args=(), daemon=None, name=None):
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


class _SyncThreading:
    Thread = _ImmediateThread


@pytest.fixture(autouse=True)
def _run_background_thread_inline(monkeypatch):
    monkeypatch.setattr(slack_routes, "threading", _SyncThreading)


@pytest.fixture(autouse=True)
def _no_real_response_url_calls(monkeypatch):
    """Default every test to a no-op ``respond_to_response_url`` — otherwise
    ``_payload``'s default ``response_url`` would make a real outbound HTTP
    call to hooks.slack.com from every test that doesn't care about it. Tests
    that DO care install their own recording spy, which simply overrides this."""

    monkeypatch.setattr(slack_routes, "respond_to_response_url", lambda url, payload: None)


def _payload(
    action_id: str,
    outcome: str,
    *,
    session_id="s1",
    recommendation_id=7,
    user_id="U1",
    response_url: str | None = "https://hooks.slack.com/actions/T1/1/abc",
    message_blocks: list[dict] | None = None,
) -> str:
    body: dict = {
        "actions": [
            {
                "action_id": action_id,
                "value": json.dumps(
                    {
                        "session_id": session_id,
                        "recommendation_id": recommendation_id,
                        "outcome": outcome,
                    }
                ),
            }
        ],
        "user": {"id": user_id},
    }
    if response_url is not None:
        body["response_url"] = response_url
    if message_blocks is not None:
        body["message"] = {"blocks": message_blocks}
    return json.dumps(body)


def _link_lookup(monkeypatch, employee_id: int | None) -> None:
    resolved = None if employee_id is None else _FakeLink(employee_id=employee_id)
    monkeypatch.setattr(
        slack_routes, "get_slack_link_by_slack_user_id", lambda session, uid: resolved
    )


def test_accept_from_the_assigned_responder_resumes_and_advances(monkeypatch) -> None:
    _link_lookup(monkeypatch, 42)
    service = _FakeService(responder_id=42, current_responder_id=42)

    response = _handle_interactivity_action(service, _payload("tekijin_accept", "accepted"))

    assert service.submitted == [
        {"session_id": "s1", "outcome": "accepted", "recommendation_id": 7}
    ]
    assert service.streamed is True
    assert "承諾しました" in json.loads(response.body)["text"]


def test_refer_button_maps_to_declined_outcome_with_its_own_message(monkeypatch) -> None:
    _link_lookup(monkeypatch, 42)
    service = _FakeService(responder_id=42, current_responder_id=42)

    response = _handle_interactivity_action(service, _payload("tekijin_refer", "declined"))

    assert service.submitted[0]["outcome"] == "declined"
    assert service.streamed is True
    assert "自分より適任" in json.loads(response.body)["text"]


def test_non_responder_slack_user_is_rejected_without_raising(monkeypatch) -> None:
    _link_lookup(monkeypatch, 99)  # linked, but not THIS session's responder
    service = _FakeService(responder_id=99, current_responder_id=42)

    response = _handle_interactivity_action(service, _payload("tekijin_accept", "accepted"))

    assert service.submitted == []
    assert service.streamed is False
    assert response.status_code == 200
    assert "権限がありません" in json.loads(response.body)["text"]


def test_unlinked_slack_user_is_rejected_without_raising(monkeypatch) -> None:
    _link_lookup(monkeypatch, None)
    service = _FakeService(responder_id=None, current_responder_id=42)

    response = _handle_interactivity_action(service, _payload("tekijin_decline", "declined"))

    assert service.submitted == []
    assert response.status_code == 200
    assert "権限がありません" in json.loads(response.body)["text"]


def test_successful_action_replaces_the_original_message_removing_the_buttons(
    monkeypatch,
) -> None:
    """The synchronous 200 body is inert for a block_actions click — Slack
    only updates the message via response_url. Without this, the buttons
    stay clickable forever even after a legitimate accept/decline."""

    _link_lookup(monkeypatch, 42)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        slack_routes, "respond_to_response_url", lambda url, payload: calls.append((url, payload))
    )
    service = _FakeService(responder_id=42, current_responder_id=42)

    _handle_interactivity_action(
        service, _payload("tekijin_accept", "accepted", response_url="https://hooks.slack.com/x")
    )

    assert len(calls) == 1
    url, sent = calls[0]
    assert url == "https://hooks.slack.com/x"
    assert sent["replace_original"] is True
    assert "承諾しました" in sent["text"]


def test_successful_action_keeps_the_consultation_text_and_only_drops_the_buttons(
    monkeypatch,
) -> None:
    """Replacing the message must not wipe the original consultation text —
    only the button row should go, with the outcome appended below it."""

    _link_lookup(monkeypatch, 42)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        slack_routes, "respond_to_response_url", lambda url, payload: calls.append((url, payload))
    )
    service = _FakeService(responder_id=42, current_responder_id=42)
    original_blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "相談内容の本文です"}},
        {"type": "actions", "elements": [{"type": "button"}]},
    ]

    _handle_interactivity_action(
        service,
        _payload(
            "tekijin_accept",
            "accepted",
            response_url="https://hooks.slack.com/x",
            message_blocks=original_blocks,
        ),
    )

    assert len(calls) == 1
    _url, sent = calls[0]
    kept_blocks = sent["blocks"]
    assert {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "相談内容の本文です"},
    } in kept_blocks
    assert not any(b.get("type") == "actions" for b in kept_blocks)
    assert "承諾しました" in kept_blocks[-1]["text"]["text"]


def test_rejected_action_sends_an_ephemeral_reply_without_touching_the_message(
    monkeypatch,
) -> None:
    """A non-responder's click must not alter the shared message (the real
    responder still needs its buttons) — only the clicker should see why
    nothing happened, via an ephemeral response_url reply."""

    _link_lookup(monkeypatch, 99)  # linked, but not this session's responder
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        slack_routes, "respond_to_response_url", lambda url, payload: calls.append((url, payload))
    )
    service = _FakeService(responder_id=99, current_responder_id=42)

    _handle_interactivity_action(
        service, _payload("tekijin_accept", "accepted", response_url="https://hooks.slack.com/x")
    )

    assert len(calls) == 1
    _url, sent = calls[0]
    assert sent.get("response_type") == "ephemeral"
    assert "replace_original" not in sent
    assert "権限がありません" in sent["text"]


def test_no_response_url_call_when_the_payload_omits_one(monkeypatch) -> None:
    _link_lookup(monkeypatch, 42)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        slack_routes, "respond_to_response_url", lambda url, payload: calls.append((url, payload))
    )
    service = _FakeService(responder_id=42, current_responder_id=42)

    _handle_interactivity_action(service, _payload("tekijin_accept", "accepted", response_url=None))

    assert calls == []


def test_malformed_payload_returns_a_friendly_200_instead_of_raising() -> None:
    service = _FakeService(responder_id=42, current_responder_id=42)

    response = _handle_interactivity_action(service, "not json")

    assert response.status_code == 200
    assert "処理できませんでした" in json.loads(response.body)["text"]


def test_advance_after_resume_drains_the_stream_when_streamable() -> None:
    service = _FakeService(responder_id=42, current_responder_id=42, is_streamable_result=True)

    _advance_after_resume(service, "s1")

    assert service.streamed is True


def test_advance_after_resume_skips_when_not_streamable() -> None:
    service = _FakeService(responder_id=42, current_responder_id=42, is_streamable_result=False)

    _advance_after_resume(service, "s1")

    assert service.streamed is False


def test_advance_after_resume_swallows_errors() -> None:
    class _Boom(_FakeService):
        def stream_events(self, session_id):
            raise RuntimeError("boom")

    service = _Boom(responder_id=42, current_responder_id=42)

    _advance_after_resume(service, "s1")  # must not raise
