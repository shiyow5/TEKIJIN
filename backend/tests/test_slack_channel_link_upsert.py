"""A pair has at most one channel row, so re-creating one must REPLACE it (#494).

`ensure_pair_channel` now discards a channel row belonging to a different Slack
workspace. Discarding it from the local variable is not enough: the row is still
in the table, and `(employee_low_id, employee_high_id)` is the primary key, so
the follow-up insert violates it. The failure lands inside a best-effort daemon
thread, so the pair silently loses Slack channels forever instead of erroring.
"""

from __future__ import annotations

import datetime as dt

from tekijin.data.db import get_sessionmaker
from tekijin.data.slack_channel_links import create_channel_link, get_channel_link

NOW = dt.datetime(2026, 9, 15, 12, 0, 0)


def test_recreating_a_pairs_channel_replaces_the_existing_row(seed_counts, engine) -> None:
    factory = get_sessionmaker(engine)
    with factory() as session:
        create_channel_link(
            session,
            31,
            32,
            thread_id=1,
            slack_channel_id="C_OLD",
            slack_team_id="T_OLD",
            now=NOW,
        )
        session.commit()

    with factory() as session:
        create_channel_link(
            session,
            31,
            32,
            thread_id=9,
            slack_channel_id="C_NEW",
            slack_team_id="T_NEW",
            now=NOW,
        )
        session.commit()

    with factory() as session:
        link = get_channel_link(session, 31, 32)
        assert link.slack_channel_id == "C_NEW"
        assert link.slack_team_id == "T_NEW"
        assert link.current_thread_id == 9
        session.delete(link)
        session.commit()


def test_pair_order_does_not_create_a_second_row(seed_counts, engine) -> None:
    # The pair key is canonicalised, so (b, a) must hit the same row.
    factory = get_sessionmaker(engine)
    with factory() as session:
        create_channel_link(
            session,
            33,
            34,
            thread_id=1,
            slack_channel_id="C_A",
            slack_team_id="T",
            now=NOW,
        )
        session.commit()
    with factory() as session:
        create_channel_link(
            session,
            34,
            33,
            thread_id=2,
            slack_channel_id="C_B",
            slack_team_id="T",
            now=NOW,
        )
        session.commit()
    with factory() as session:
        link = get_channel_link(session, 33, 34)
        assert link.slack_channel_id == "C_B"
        session.delete(link)
        session.commit()
