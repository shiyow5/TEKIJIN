"""Read-only dashboard aggregations (recommendation load, topic mix, activity).

Pure SQL over the seeded schema, returned as a plain dict for the API layer to
wrap in Pydantic. Every query has a deterministic ``ORDER BY`` (with a
tiebreaker) so the output is stable.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from tekijin.data.feedback import VALID_STAGES, feedback_counts_by_stage
from tekijin.models.tables import (
    Answer,
    Employee,
    EvalRun,
    Event,
    OfflineConsult,
    Question,
    Recommendation,
)
from tekijin.scorer.weights import OFFLINE_CONSULT_POSITIVE_RESOLUTIONS

# Routes that resolve a question WITHOUT contacting a live person — the numerator
# of the self-resolution rate (product-spec 画面5). Only ``document`` qualifies in
# the current graph: ``prior_answer`` pins the past responder and still runs
# through C6/C7 to the ``send`` human interrupt, so it is NOT self-resolved.
# (Counting a genuinely person-free prior_answer path would need runtime tracking
# of whether the asker accepted the past answer without asking again — a follow-up.)
_SELF_RESOLVED_ROUTES = ("document",)


def top_answerers(session: Session, *, limit: int = 5) -> list[dict[str, Any]]:
    """Who has answered the most (a proxy for workload distribution), busiest-first.

    Extracted from :func:`dashboard_summary` (画面5 負荷分散) so it is callable
    on its own. A per-responder "回答者別の件数" panel was tried on the
    knowledge list (#293, #301) but removed after review — that ranking is
    the dashboard's job, not a knowledge browser's (PR #340 review) — so this
    is dashboard-only again in practice.
    """

    load_stmt = (
        select(Answer.responder_id, Employee.name, func.count().label("c"))
        .join(Employee, Employee.id == Answer.responder_id)
        .group_by(Answer.responder_id, Employee.name)
        .order_by(func.count().desc(), Answer.responder_id)
        .limit(limit)
    )
    return [
        {"employee_id": rid, "name": name, "answer_count": count}
        for rid, name, count in session.execute(load_stmt)
    ]


def dashboard_summary(
    session: Session,
    *,
    top_responders: int = 5,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Aggregate counts, load distribution, topic mix, and outcome ratios.

    Aggregate-only by design (product-spec §241-251): no individual records are
    enumerated — the dashboard summarises usage, it is not an audit log.
    """

    total_employees = session.scalar(select(func.count()).select_from(Employee)) or 0
    total_questions = session.scalar(select(func.count()).select_from(Question)) or 0
    total_answers = session.scalar(select(func.count()).select_from(Answer)) or 0
    recommendation_count = session.scalar(select(func.count()).select_from(Recommendation)) or 0

    # Load: who has answered the most (a proxy for workload distribution).
    # A zero/negative limit would empty the list and make `_top_responder_share`
    # read 0.0 — a headline KPI silently wrong rather than absent (#76 review).
    assert top_responders >= 1, "top_responders must be >= 1"
    answers_per_responder = top_answerers(session, limit=top_responders)

    # Topic distribution over the questions' topic arrays.
    topic_col = func.unnest(Question.topics).label("topic")
    topic_stmt = (
        select(topic_col, func.count().label("c"))
        .group_by(topic_col)
        .order_by(func.count().desc(), topic_col)
    )
    topic_distribution = [
        {"topic": topic, "count": count} for topic, count in session.execute(topic_stmt)
    ]

    # Outcome aggregation (accept/decline/pending) + acceptance ratio — the
    # "使うほど育つ" signal, as aggregates only (no per-record listing).
    outcome_stmt = select(Recommendation.outcome, func.count()).group_by(Recommendation.outcome)
    outcome_counts = {outcome: count for outcome, count in session.execute(outcome_stmt)}
    accepted = outcome_counts.get("accepted", 0)
    declined = outcome_counts.get("declined", 0)
    pending = outcome_counts.get(None, 0)
    decided = accepted + declined
    acceptance_rate = (accepted / decided) if decided else 0.0

    return {
        "total_employees": total_employees,
        "total_questions": total_questions,
        "total_answers": total_answers,
        "recommendation_count": recommendation_count,
        "recommendation_outcomes": {
            "accepted": accepted,
            "declined": declined,
            "pending": pending,
        },
        "acceptance_rate": acceptance_rate,
        "self_resolution_rate": _self_resolution_rate(session),
        "avg_resolution_hours": _avg_resolution_hours(session),
        "top_responder_share": _top_responder_share(answers_per_responder, total_answers),
        "processing_latency": _processing_latency(session),
        "latest_eval": _latest_eval(session),
        "answers_per_responder": answers_per_responder,
        "topic_distribution": topic_distribution,
        "feedback_by_stage": _feedback_by_stage(session),
        "knowledge_accumulation": _knowledge_accumulation(session, now),
    }


# --------------------------------------------------------------------------- #
# #294: 蓄積メトリクス — how much tacit knowledge became explicit, and when
# --------------------------------------------------------------------------- #
# How many months of history the trend carries (this month plus the five before).
_ACCUMULATION_MONTHS = 6


def _month_key(moment: dt.datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


def _month_start(moment: dt.datetime) -> dt.datetime:
    return dt.datetime(moment.year, moment.month, 1)


def _months_back(start: dt.datetime, count: int) -> dt.datetime:
    """First day of the month ``count`` months before ``start``'s month."""

    total = start.year * 12 + (start.month - 1) - count
    return dt.datetime(total // 12, total % 12 + 1, 1)


def _captured_answers_stmt():
    """``answers`` rows the RUNTIME produced, not the ones the fixtures shipped.

    The product seeds 150 answers, 16 of them dated in the current month, so
    counting the table would make 「今月の形式化知識量」 read 16 on a database nobody
    has used yet — wrong in the flattering direction, which is the worst way for a
    headline KPI to be wrong.

    Provenance is knowable without a marker column: a captured answer (#274) is
    written when a responder ACCEPTS a hand-off, and ``seed.py`` never inserts
    ``recommendations`` (it only TRUNCATEs them). So "has an accepted
    recommendation for this question and this responder" separates the two
    exactly, and keeps doing so as the fixtures change.
    """

    return (
        select(Answer.created_at)
        .join(
            Recommendation,
            and_(
                Recommendation.question_id == Answer.question_id,
                Recommendation.employee_id == Answer.responder_id,
                Recommendation.outcome == "accepted",
            ),
        )
        .where(Answer.created_at.isnot(None))
    )


def _knowledge_accumulation(session: Session, now: dt.datetime | None) -> dict[str, Any]:
    """Newly formalized knowledge per month, and the share of hand-offs that left any.

    Two sources, both runtime-only:

    * **captured answers** (#274) — the responder's own text, saved when they
      accept, which the retriever can then reuse.
    * **consult retrospectives** (#247) — a 直接相談 leaves no transcript, so the
      asker's write-up IS the artefact. Fixtures write none of these either.

    ``capture_rate`` is the recovery rate: of the hand-offs someone accepted this
    month, how many left knowledge behind. It answers whether the loop is closing,
    which raw counts cannot — they only grow.

    ``now`` is injected (the scorer's convention) so the month boundary is
    testable; it defaults to the process clock.
    """

    now = now or dt.datetime.now()
    this_start = _month_start(now)
    last_start = _months_back(this_start, 1)
    window_start = _months_back(this_start, _ACCUMULATION_MONTHS - 1)

    def _count(stmt, column, start: dt.datetime, end: dt.datetime | None) -> int:
        scoped = stmt.where(column >= start)
        if end is not None:
            scoped = scoped.where(column < end)
        return session.scalar(select(func.count()).select_from(scoped.subquery())) or 0

    answers_stmt = _captured_answers_stmt()
    # `unresolved` is stored but is inert everywhere else (断り≠非専門, see
    # OfflineConsult) — a write-up saying "聞いたが分からなかった" is not knowledge,
    # and counting it would inflate the headline in the flattering direction.
    consults_stmt = select(OfflineConsult.created_at).where(
        OfflineConsult.created_at.isnot(None),
        OfflineConsult.resolution.in_(OFFLINE_CONSULT_POSITIVE_RESOLUTIONS),
    )

    captured_answers = _count(answers_stmt, Answer.created_at, this_start, None)
    retrospectives = _count(consults_stmt, OfflineConsult.created_at, this_start, None)
    last_month = _count(answers_stmt, Answer.created_at, last_start, this_start) + _count(
        consults_stmt, OfflineConsult.created_at, last_start, this_start
    )

    # Dense series: a month with nothing accumulated is a 0, never a missing point
    # (a sparse series turns a gap into a slope once it is drawn).
    buckets = {
        _month_key(_months_back(this_start, offset)): 0
        for offset in reversed(range(_ACCUMULATION_MONTHS))
    }
    for stmt, column in (
        (answers_stmt, Answer.created_at),
        (consults_stmt, OfflineConsult.created_at),
    ):
        rows = session.execute(stmt.where(column >= window_start)).all()
        for (created,) in rows:
            key = _month_key(created)
            if key in buckets:
                buckets[key] += 1

    # The rate's two halves must be counted on the SAME clock. `captured_answers`
    # above is keyed on when the ANSWER was written (right for "knowledge created
    # this month"), but `recommendations.created_at` is when the hand-off was
    # SHOWN, not accepted — there is no `accepted_at`. Mixing them lets a hand-off
    # shown last month and answered this month land in the numerator and not the
    # denominator, so the rate could read above 100%. So the rate is computed from
    # one population: hand-offs shown-and-accepted this month, and how many of
    # THOSE left an answer behind.
    accepted_stmt = select(Recommendation.id).where(
        Recommendation.outcome == "accepted",
        Recommendation.created_at >= this_start,
    )
    accepted_handoffs = (
        session.scalar(select(func.count()).select_from(accepted_stmt.subquery())) or 0
    )
    # A 直接相談 leaves NO answers row — that is its definition (#247): it happens
    # face to face, so the retrospective IS the artefact. Counting only `answers`
    # scored every properly-written-up direct consult as an UNCAPTURED hand-off,
    # contradicting this same function counting it as knowledge a few lines below.
    # Either record closes the loop. DISTINCT because a question with two answers
    # from one responder would otherwise push the rate above 1.0 (nothing in the
    # schema forbids that pair).
    left_a_record = or_(
        select(Answer.id)
        .where(
            Answer.question_id == Recommendation.question_id,
            Answer.responder_id == Recommendation.employee_id,
        )
        .exists(),
        select(OfflineConsult.id)
        .where(
            OfflineConsult.question_id == Recommendation.question_id,
            OfflineConsult.responder_id == Recommendation.employee_id,
        )
        .exists(),
    )
    captured_from_those = (
        session.scalar(
            select(func.count(func.distinct(Recommendation.id)))
            .select_from(Recommendation)
            .where(
                Recommendation.outcome == "accepted",
                Recommendation.created_at >= this_start,
                left_a_record,
            )
        )
        or 0
    )
    # 0/0 reads as 0.0, not 1.0: "nothing was handed off" must never render as
    # "everything was captured".
    capture_rate = (captured_from_those / accepted_handoffs) if accepted_handoffs else 0.0

    return {
        "this_month": captured_answers + retrospectives,
        "last_month": last_month,
        "captured_answers": captured_answers,
        "consult_retrospectives": retrospectives,
        "accepted_handoffs": accepted_handoffs,
        "capture_rate": capture_rate,
        "monthly": [{"month": key, "count": count} for key, count in buckets.items()],
    }


def _feedback_by_stage(session: Session) -> dict[str, int]:
    """Feedback counts per pipeline stage + total (#237 — どの段でどれだけずれているか).

    Every stage (c1/c6/c7) is present with an explicit 0 so the dashboard shape is
    stable before any feedback exists.
    """

    counts = feedback_counts_by_stage(session)
    by_stage = {stage: counts.get(stage, 0) for stage in VALID_STAGES}
    by_stage["total"] = sum(by_stage.values())
    return by_stage


def _self_resolution_rate(session: Session) -> float:
    """Share of routed questions resolved via an auxiliary route (画面5 自己解決率).

    Denominator is questions that carry a route (i.e. went through the system);
    numerator is those routed to ``prior_answer`` / ``document`` (no new live
    hand-off). 0.0 when nothing has been routed yet (pre-seeded questions have a
    NULL route).
    """

    routed = (
        session.scalar(select(func.count()).select_from(Question).where(Question.route.isnot(None)))
        or 0
    )
    if not routed:
        return 0.0
    # A question actually resolved by a PERSON — an accepted rank-1 recommendation,
    # any answer row, or a seeded ``status="answered"`` — is never a self-resolution,
    # even if the asker also clicked "自分で解決した" (a race: self-resolve while the
    # hand-off was pending, then a responder accepts). Mirrors history.py's
    # ``by_person`` precedence so the KPI cannot be inflated (#159 review).
    by_person = or_(
        Question.status == "answered",
        Question.id.in_(select(Answer.question_id)),
        Question.id.in_(
            select(Recommendation.question_id).where(
                Recommendation.rank == 1, Recommendation.outcome == "accepted"
            )
        ),
    )
    # Numerator: an auxiliary self-resolving route (document) OR an explicit
    # "自分で解決した" signal on a question no person actually resolved (#159).
    self_resolved = (
        session.scalar(
            select(func.count())
            .select_from(Question)
            .where(
                Question.route.isnot(None),
                or_(
                    Question.route.in_(_SELF_RESOLVED_ROUTES),
                    and_(Question.resolution_kind == "self", not_(by_person)),
                ),
            )
        )
        or 0
    )
    return self_resolved / routed


def _avg_resolution_hours(session: Session) -> float | None:
    """Mean hours from a question to its resolution (画面5 平均解決時間).

    Resolution time is the runtime ``questions.resolved_at`` when present (a live
    accept / self-resolution, #97), otherwise the earliest ``answers`` row (the
    seeded / historical Q&A). The earliest answer is used so a later follow-up
    answer does not inflate the time. ``None`` when nothing is resolved yet.
    """

    first_answer = (
        select(Answer.question_id.label("qid"), func.min(Answer.created_at).label("solved"))
        .group_by(Answer.question_id)
        .subquery()
    )
    # Runtime resolution wins; fall back to the first answer for seeded history.
    solved = func.coalesce(Question.resolved_at, first_answer.c.solved)
    hours = func.extract("epoch", solved - Question.created_at) / 3600.0
    return session.scalar(
        select(func.avg(hours))
        .select_from(Question)
        .outerjoin(first_answer, first_answer.c.qid == Question.id)
        .where(Question.created_at.isnot(None), solved.isnot(None))
    )


def _processing_latency(session: Session) -> dict[str, Any]:
    """p50/p95 of per-question AI processing time in ms (画面5 / #177).

    Sums each question's recorded stage durations (``events.ended_at -
    started_at``) — so human-wait gaps between run segments are excluded — then
    takes the median / 95th percentile across questions. Empty (no runs recorded
    yet) yields None percentiles with ``sample_size`` 0.
    """

    stage_ms = func.extract("epoch", Event.ended_at - Event.started_at) * 1000.0
    per_question = (
        select(func.sum(stage_ms).label("ms"))
        .where(Event.started_at.isnot(None), Event.ended_at.isnot(None))
        .group_by(Event.question_id)
        .subquery()
    )
    p50, p95, n = session.execute(
        select(
            func.percentile_cont(0.5).within_group(per_question.c.ms.asc()),
            func.percentile_cont(0.95).within_group(per_question.c.ms.asc()),
            func.count(),
        )
    ).one()
    return {
        "p50_ms": round(p50) if p50 is not None else None,
        "p95_ms": round(p95) if p95 is not None else None,
        "sample_size": n,
    }


def _top_responder_share(answers_per_responder: list[dict[str, Any]], total_answers: int) -> float:
    """Concentration on the single busiest responder (画面5 負荷分散).

    ``answers_per_responder`` is already ordered busiest-first, so its head is the
    max. **The denominator is the total answer COUNT, not the sum of this (possibly
    truncated) list** — so the KPI does not move when the caller changes how many
    responders it asks for (#76 made that a query param). The one way to break that
    is to pass ``top_responders=0``: the list comes back empty and this silently
    reads 0.0 rather than the real concentration. The route bounds the param at
    ``ge=1``; ``top_responders`` is asserted below so a direct caller cannot do it
    quietly either. 0.0 when there are no answers. NOTE: like 平均解決時間 this reads the
    ``answers`` distribution (seed / historical), so it does not yet move with
    live routing (API hand-offs write ``recommendations``, not ``answers``); and
    the spec's naive-order baseline comparison (product-spec §246) is an offline
    measure. Both are tracked as follow-ups (#97).
    """

    if not total_answers or not answers_per_responder:
        return 0.0
    return answers_per_responder[0]["answer_count"] / total_answers


def _latest_eval(session: Session) -> dict[str, Any] | None:
    """The most recent stored evaluation snapshot (画面5 推薦精度), or ``None``.

    ``None`` until ``python -m tekijin.eval`` has persisted a run — the dashboard
    then shows a clear "未計測" state rather than a fabricated number.
    """

    row = session.execute(
        select(EvalRun).order_by(EvalRun.created_at.desc(), EvalRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "top1_accuracy": row.top1_accuracy,
        "recall_at_3": row.recall_at_3,
        "mrr": row.mrr,
        "route_accuracy": row.route_accuracy,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
