"""Tests for chat -> conversation grouping (#448).

Unit tests drive :func:`group_rows` with lightweight row stubs (no DB); an
integration test exercises :func:`chat_conversation_sources` against the seeded
``employee_chat_history`` fixtures.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tekijin.knowledge.chat import (
    DEFAULT_EXCLUDED_CHANNELS,
    chat_conversation_sources,
    group_rows,
)


@dataclass
class _Row:
    """Minimal stand-in for EmployeeChatHistory (only the fields group_rows reads)."""

    id: int
    channel: str | None
    sent_at: dt.datetime | None
    sender_employee_id: int = 1
    message: str | None = "こんにちは"


_T0 = dt.datetime(2026, 5, 1, 9, 0, 0)


def _row(i: int, channel: str, minutes: int, msg: str = "内容", sender: int = 1) -> _Row:
    return _Row(
        id=i,
        channel=channel,
        sent_at=_T0 + dt.timedelta(minutes=minutes),
        message=msg,
        sender_employee_id=sender,
    )


def test_group_rows_groups_within_gap_and_labels_speakers() -> None:
    rows = [
        _row(1, "IT問い合わせ", 0, "VPNに繋がらない", sender=5),
        _row(2, "IT問い合わせ", 3, "クライアント設定を見直して", sender=9),
    ]
    out = group_rows(rows, gap_minutes=30, min_messages=2)
    assert len(out) == 1
    src = out[0]
    assert src.source_type == "chat"
    assert src.source_id == "chat_IT問い合わせ_1"  # keyed on channel + first id
    assert src.topics == ()  # chat has no precomputed tags
    # Transcript labels each turn by sender so the LLM sees question -> answer.
    assert src.text == "社員5: VPNに繋がらない\n社員9: クライアント設定を見直して"


def test_group_rows_splits_on_time_gap() -> None:
    rows = [
        _row(1, "営業部-連絡", 0),
        _row(2, "営業部-連絡", 5),
        _row(3, "営業部-連絡", 200),  # >30min later -> new conversation
        _row(4, "営業部-連絡", 205),
    ]
    out = group_rows(rows, gap_minutes=30, min_messages=2)
    assert [s.source_id for s in out] == ["chat_営業部-連絡_1", "chat_営業部-連絡_3"]


def test_group_rows_splits_on_channel_change() -> None:
    rows = [
        _row(1, "経理・総務", 0),
        _row(2, "経理・総務", 2),
        _row(3, "開発部-連絡", 4),
        _row(4, "開発部-連絡", 6),
    ]
    out = group_rows(rows, gap_minutes=30, min_messages=2)
    assert [s.source_id for s in out] == ["chat_経理・総務_1", "chat_開発部-連絡_3"]


def test_group_rows_drops_runs_below_min_messages() -> None:
    # A lone message (a broadcast) is not a conversation.
    rows = [_row(1, "営業部-連絡", 0), _row(2, "営業部-連絡", 200)]
    assert group_rows(rows, gap_minutes=30, min_messages=2) == []


def test_group_rows_excludes_noise_channels_by_default() -> None:
    rows = [
        _row(1, "雑談", 0, "コーヒー行きましょう"),
        _row(2, "雑談", 2, "いいですね"),
        _row(3, "general", 4, "在宅です"),
        _row(4, "general", 6, "了解"),
    ]
    assert group_rows(rows) == []
    assert "雑談" in DEFAULT_EXCLUDED_CHANNELS and "general" in DEFAULT_EXCLUDED_CHANNELS


def test_group_rows_caps_long_runs_at_max_messages() -> None:
    rows = [_row(i, "IT問い合わせ", i) for i in range(1, 6)]  # 5 messages, 1min apart
    out = group_rows(rows, gap_minutes=30, min_messages=1, max_messages=2)
    # 5 messages capped at 2 -> chunks of [1,2], [3,4], [5]
    assert [s.source_id for s in out] == [
        "chat_IT問い合わせ_1",
        "chat_IT問い合わせ_3",
        "chat_IT問い合わせ_5",
    ]


def test_group_rows_skips_empty_or_untimed_rows() -> None:
    rows = [
        _row(1, "IT問い合わせ", 0, "質問です"),
        _Row(id=2, channel="IT問い合わせ", sent_at=None, message="タイムスタンプ無し"),
        _Row(id=3, channel="IT問い合わせ", sent_at=_T0 + dt.timedelta(minutes=1), message="   "),
        _row(4, "IT問い合わせ", 2, "回答です"),
    ]
    out = group_rows(rows, gap_minutes=30, min_messages=2)
    assert len(out) == 1
    assert out[0].text == "社員1: 質問です\n社員1: 回答です"


def test_group_rows_respects_limit() -> None:
    rows = [
        _row(1, "経理・総務", 0),
        _row(2, "経理・総務", 2),
        _row(3, "開発部-連絡", 4),
        _row(4, "開発部-連絡", 6),
        _row(5, "営業部-連絡", 8),
        _row(6, "営業部-連絡", 10),
    ]
    out = group_rows(rows, gap_minutes=30, min_messages=2, limit=2)
    assert len(out) == 2


def test_chat_conversation_sources_reads_seeded_history(seed_counts, session) -> None:
    # The seeded employee_chat_history yields well-formed conversation sources.
    sources = chat_conversation_sources(session, limit=20)
    assert sources, "seeded chat should group into at least one conversation"
    for s in sources:
        assert s.source_type == "chat" and s.source_id.startswith("chat_")
        assert s.topics == ()  # inferred at extraction time, not from source
        assert s.text and "社員" in s.text
        # noise channels are excluded
        assert not s.source_id.startswith("chat_雑談_")
        assert not s.source_id.startswith("chat_general_")
