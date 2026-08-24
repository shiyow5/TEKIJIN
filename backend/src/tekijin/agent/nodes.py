"""The C1-C8 graph nodes (plus terminals and control nodes).

Each node is a method returning a *partial* :class:`AgentState` update. Deps
(LLM stubs, embedder, retriever, scorer) are injected so the same graph runs on
the deterministic stubs today and real models later. The two nodes that pause for
human input — ``ask`` (C2 clarification) and ``send`` (responder outcome) — use
LangGraph ``interrupt`` and resume via ``Command(resume=...)``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any, cast

from langgraph.types import interrupt

from tekijin.agent.protocols import (
    DraftModel,
    IntentModel,
    IntentResult,
    Retriever,
    SufficiencyModel,
)
from tekijin.agent.route import PRIOR_ANSWER, decide_route
from tekijin.agent.safety import scan_disallowed
from tekijin.agent.state import AgentState, empty_retrieval
from tekijin.agent.stubs import (
    INTENT_CONFIDENCE_THRESHOLD,
    MAX_FOLLOWUPS,
    collect_known_values,
)
from tekijin.retrieval.embedding import QUERY, Embedder
from tekijin.scorer.scorer import ExpertiseScorer

_QUESTION_TYPE_DEFAULT = "製品QA"


def _top_by_score(items: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The item with the highest ``score`` (deterministic, order-independent)."""

    if not items:
        return None
    return max(items, key=lambda item: float(item.get("score", 0.0)))


def draft_context(state: Mapping[str, Any]) -> tuple[list[str], dict[str, str]]:
    """``(missing, known_values)`` for drafting a hand-off to a given responder.

    Factored out of :meth:`AgentNodes.c7_draft` so the ``/handoff/select``
    reselect path (#A1) can regenerate a draft for a different candidate using
    the exact same slot logic, without duplicating it.
    """

    question_type = state.get("question_type", _QUESTION_TYPE_DEFAULT)
    products = state.get("products") or []
    known_values = collect_known_values(state["question"], question_type, products)
    # A slot must never appear as both a filled premise and an open gap: drop
    # from `missing` anything we now surface under known_values, so the draft
    # cannot show the same slot as "確認済み" and "補足いただきたい" at once
    # (defensive dedup — C2 already recomputes `missing` on the re-understood
    # question via the ask->c1_intent edge, so they normally agree) (#175).
    missing = [slot for slot in (state.get("missing") or []) if slot not in known_values]
    return missing, known_values


class AgentNodes:
    """Bundles the graph's node implementations around their dependencies."""

    def __init__(
        self,
        *,
        intent_model: IntentModel,
        sufficiency_model: SufficiencyModel,
        draft_model: DraftModel,
        embedder: Embedder,
        retriever: Retriever,
        scorer: ExpertiseScorer,
    ) -> None:
        self._intent = intent_model
        self._sufficiency = sufficiency_model
        self._draft = draft_model
        self._embedder = embedder
        self._retriever = retriever
        self._scorer = scorer

    # -- entry: validate input, reset per-question control fields ---------
    def reset(self, state: AgentState) -> AgentState:
        """Validate the new question and clear per-question control fields.

        Runs at the START of every fresh ``invoke`` — but NOT on ``resume`` (a
        ``Command(resume=...)`` continues from the interrupted node, bypassing
        START). So a second question on the same ``thread_id`` starts clean
        (no inherited ``followup_count`` / ``declined_ids`` / stale route or
        answer), while the clarification and decline loops keep their state.
        """

        question = state.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question is required and must be a non-empty string")
        now = state.get("now")
        if not isinstance(now, dt.datetime):
            raise ValueError("now is required and must be a datetime")
        if now.tzinfo is not None:
            raise ValueError("now must be timezone-naive (matches stored timestamps)")

        return {
            "followup_count": 0,
            "declined_ids": [],
            "out_of_scope": False,
            "sufficient": False,
            "topics": [],
            "products": [],
            "situation": None,
            "question_type": _QUESTION_TYPE_DEFAULT,
            "intent_confidence": 0.0,
            "intent_unresolved": False,
            "missing": [],
            "followup_question": None,
            "retrieval": empty_retrieval(),
            "route": "person",
            "route_reason": "",
            "route_confidence": 0.0,
            "prior_answer_note": None,
            "pinned_responder_id": None,
            "recommendations": [],
            "draft": None,
            "outcome": None,
            "answer": None,
            "query_vector": [],
            # Per-question durable persistence identity: clear the rec ids
            # (a fresh question has no shown recommendations yet). ``question_id``
            # is intentionally NOT reset here — it is supplied on the fresh
            # ``invoke`` input and must survive this merge.
            "recommendation_ids": [],
            "primary_recommendation_id": None,
            "last_event": None,
        }

    # -- C1: intent understanding (LLM stub) ------------------------------
    def c1_intent(self, state: AgentState) -> AgentState:
        # Deterministic safety net BEFORE trusting the (prompt-dependent) model:
        # clear PII/secret solicitation or prompt injection is refused regardless of
        # which intent model is wired, so a model swap cannot regress it (#155). It
        # only ADDS rejections; softer out_of_scope cases stay the model's call.
        if scan_disallowed(state["question"]) is not None:
            return {
                "topics": [],
                "products": [],
                "situation": None,
                "question_type": _QUESTION_TYPE_DEFAULT,
                "out_of_scope": True,
                "intent_confidence": 0.0,
            }
        result = self._intent.analyze(state["question"], state.get("asker"))
        return {
            "topics": result.topics,
            "products": result.products,
            "situation": result.situation,
            "question_type": result.question_type,
            "out_of_scope": result.out_of_scope,
            "intent_confidence": result.confidence,
        }

    # -- C2: sufficiency check (LLM stub) ---------------------------------
    def c2_sufficiency(self, state: AgentState) -> AgentState:
        intent = IntentResult(
            topics=state.get("topics", []),
            products=state.get("products", []),
            situation=state.get("situation"),
            question_type=state.get("question_type", _QUESTION_TYPE_DEFAULT),
            out_of_scope=state.get("out_of_scope", False),
            confidence=state.get("intent_confidence", 0.0),
        )
        followup_count = state.get("followup_count", 0)
        result = self._sufficiency.check(state["question"], intent, followup_count)
        # Graph-level termination guarantee: never ask more than MAX_FOLLOWUPS,
        # whatever the (possibly future vLLM) model returns.
        capped = followup_count >= MAX_FOLLOWUPS
        # Safety valve (#113): once C1 has confidently identified the topic, we can
        # already decide WHO to route to — the responder can ask for any missing
        # detail (現行製品/対象拠点数…). So don't let C2 block a confident, on-topic
        # consultation on estimate-style slots, no matter how a (prompt-sensitive)
        # model feels. This overrides the model, but never fires on a vague/low-signal
        # request (no topic, or confidence below threshold), which still clarifies.
        can_route = bool(intent.topics) and intent.confidence >= INTENT_CONFIDENCE_THRESHOLD
        sufficient = result.sufficient or capped or can_route
        # If we have already asked once (capped) and STILL have no topic, the
        # intent is unresolved. Rather than silently search on nothing and land in
        # no_candidate, flag it so the graph routes to an explicit "couldn't
        # identify the request" terminal (see _after_c2 / unresolved_intent).
        intent_unresolved = capped and not (state.get("topics") or [])
        return {
            "sufficient": sufficient,
            "missing": result.missing,
            "followup_question": result.followup_question,
            "intent_unresolved": intent_unresolved,
        }

    # -- clarification: pause and ask the user one question ---------------
    def ask(self, state: AgentState) -> AgentState:
        reply = interrupt(
            {
                "followup_question": state.get("followup_question"),
                "missing": state.get("missing", []),
            }
        )
        # Only fold in a genuine, non-empty text reply; never interpolate a
        # non-string payload into the question.
        reply_text = reply.strip() if isinstance(reply, str) else ""
        enriched = f"{state['question']} {reply_text}".strip() if reply_text else state["question"]
        return {"question": enriched, "followup_count": state.get("followup_count", 0) + 1}

    # -- C3: embed the query ----------------------------------------------
    def c3_embed(self, state: AgentState) -> AgentState:
        vector = self._embedder.encode([state["question"]], kind=QUERY)[0]
        return {"query_vector": vector}

    # -- C4: hybrid retrieval ---------------------------------------------
    def c4_retrieve(self, state: AgentState) -> AgentState:
        # Reuse the C3 embedding so the dense channels do not re-embed the query
        # (halves embedding calls under a real vLLM; BM25 still uses raw text).
        retrieval = self._retriever.search(
            state["question"], query_vector=state.get("query_vector")
        )
        return {"retrieval": retrieval}

    # -- C5: route decision (deterministic) -------------------------------
    def c5_route(self, state: AgentState) -> AgentState:
        decision = decide_route(state.get("retrieval") or empty_retrieval())
        return {
            "route": decision.route,
            "route_reason": decision.reason,
            "route_confidence": decision.confidence,
        }

    # -- prior_answer (補助): pin the past responder, then hand off --------
    def prior_answer(self, state: AgentState) -> AgentState:
        past = (state.get("retrieval") or empty_retrieval())["past_answers"]
        top = _top_by_score(past)
        responder_id = top.get("responder_id") if top else None
        note = (
            f"過去に社員ID {responder_id} が類似の質問に回答しています。本人に取り次ぎます。"
            if responder_id is not None
            else "過去の類似回答を参照します。"
        )
        # Pin the responder so C6/C7 hand off to THEM (本人に追加で聞く), rather
        # than letting a higher-scoring different person win.
        return {"prior_answer_note": note, "pinned_responder_id": responder_id}

    # -- C6: expertise scorer (deterministic) -----------------------------
    def c6_score(self, state: AgentState) -> AgentState:
        topics = state.get("topics") or []
        retrieval = state.get("retrieval") or empty_retrieval()
        declined = state.get("declined_ids") or []
        pinned = state.get("pinned_responder_id")
        asker = state.get("asker")
        asker_id = asker.get("id") if asker else None
        # A non-empty `recommendations` on entry means this is a reroute backfill
        # (see `reroute`, below): those candidates already survived a decline and
        # keep their rank/score/reasons untouched — only the freed slot(s) are
        # topped up from a fresh rank() call, never a full rescore (#D5/#206).
        existing = state.get("recommendations") or []
        # prior_answer hands off to the pinned past responder — UNTIL they decline,
        # and never if the pin IS the asker (they cannot answer their own question).
        # In either case drop the pin and fall back to the general candidate pool
        # (never dead-end on a single decline or a self-referential pin).
        pool: list[int]
        if (
            state.get("route") == PRIOR_ANSWER
            and pinned is not None
            and pinned not in declined
            and pinned != asker_id
        ):
            pool = [pinned]
        else:
            pool = retrieval.get("candidate_people") or []
        existing_ids = {r["person_id"] for r in existing}
        candidates = [p for p in pool if p not in declined and p not in existing_ids]

        top_k = 3
        remaining = top_k - len(existing)
        if not topics or remaining <= 0 or not candidates:
            # Nothing to add: keep whatever survived the decline (possibly fewer
            # than 3), or [] on a genuinely fresh run with no candidates at all.
            return {"recommendations": existing}

        # All topics feed the scorer (aggregated topic_fit), not just topics[0].
        result = self._scorer.rank(topics, candidates, asker_id, state["now"], top_k=remaining)
        # The scorer now returns typed ScoredCandidate rows; AgentState keeps the
        # looser list[dict[str, Any]] (also written as plain dicts elsewhere), so
        # narrow the TypedDict-invariance gap with a cast — identical at runtime.
        fresh = cast("list[dict[str, Any]]", result["recommendations"])
        return {"recommendations": existing + fresh}

    # -- C7: draft the request (LLM stub) ---------------------------------
    def c7_draft(self, state: AgentState) -> AgentState:
        top = (state.get("recommendations") or [])[0]
        missing, known_values = draft_context(state)
        draft = self._draft.draft(
            state["question"],
            top,
            state.get("asker"),
            missing,
            situation=state.get("situation"),
            topics=state.get("topics") or [],
            known_values=known_values,
        )
        return {"draft": draft}

    # -- send: pause for the responder's outcome --------------------------
    def send(self, state: AgentState) -> AgentState:
        recs = state.get("recommendations") or []
        top = recs[0] if recs else None
        outcome = interrupt({"draft": state.get("draft"), "responder": top})
        return {"outcome": outcome}

    # -- reroute: the top pick declined; try the next candidate -----------
    def reroute(self, state: AgentState) -> AgentState:
        recs = state.get("recommendations") or []
        declined = list(state.get("declined_ids") or [])
        if recs:
            declined.append(recs[0]["person_id"])
        # Keep the already-shown survivors (rank 2/3, unchanged) instead of
        # wiping the whole set: c6_score backfills only the freed slot rather
        # than rescoring everyone from scratch (#D5/#206).
        kept = recs[1:]
        return {"declined_ids": declined, "outcome": None, "draft": None, "recommendations": kept}

    # -- C8: graph update (minimal, deterministic) ------------------------
    def c8_update(self, state: AgentState) -> AgentState:
        # The person_topic_edges online write lands in a later issue; here C8 is a
        # deterministic no-op beyond recording the successful hand-off, so the run
        # stays reproducible.
        recs = state.get("recommendations") or []
        name = recs[0]["name"] if recs else "担当者"
        return {"answer": f"{name}さんに取り次ぎました。回答をお待ちください。"}

    # -- terminals --------------------------------------------------------
    def out_of_scope(self, state: AgentState) -> AgentState:
        return {
            "answer": "恐れ入りますが、こちらは業務の範囲外のご質問のようです。"
            "社内の担当窓口にご確認ください。"
        }

    def document(self, state: AgentState) -> AgentState:
        docs = (state.get("retrieval") or empty_retrieval())["documents"]
        top = _top_by_score(docs)
        doc_id = top["doc_id"] if top else None
        where = f"（文書ID: {doc_id}）" if doc_id else ""
        answer = f"社内文書に該当がありそうです{where}。該当箇所をご確認ください。"
        # #279: C6 ran on the document route too, so a real expert behind a
        # weak-profile document is offered as a fallback rather than dead-ending
        # at zero person-recall. Self-resolution stays the main line (no hand-off
        # interrupt); the person is a "if this does not solve it" backstop.
        recs = state.get("recommendations") or []
        if recs:
            answer += f"（解決しない場合は{recs[0]['name']}さんにも取り次げます）"
        return {"answer": answer, "document_id": doc_id}

    def no_candidate(self, state: AgentState) -> AgentState:
        return {
            "answer": "現時点で適任者が見つかりませんでした。条件を変えて、もう一度お試しください。"
        }

    def unresolved_intent(self, state: AgentState) -> AgentState:
        # Reached when even after one clarification we could not identify a topic.
        # Fail gracefully instead of silently returning "no expert found".
        return {
            "answer": "ご相談内容を特定できませんでした。恐れ入りますが、"
            "具体的なご相談内容をお知らせいただくか、社内の担当窓口にご確認ください。"
        }
