"""The C1-C8 graph nodes (plus terminals and control nodes).

Each node is a method returning a *partial* :class:`AgentState` update. Deps
(LLM stubs, embedder, retriever, scorer) are injected so the same graph runs on
the deterministic stubs today and real models later. The two nodes that pause for
human input — ``ask`` (C2 clarification) and ``send`` (responder outcome) — use
LangGraph ``interrupt`` and resume via ``Command(resume=...)``.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from tekijin.agent.protocols import DraftModel, IntentModel, IntentResult, SufficiencyModel
from tekijin.agent.route import decide_route
from tekijin.retrieval.embedding import QUERY, Embedder
from tekijin.scorer.scorer import ExpertiseScorer


class AgentNodes:
    """Bundles the graph's node implementations around their dependencies."""

    def __init__(
        self,
        *,
        intent_model: IntentModel,
        sufficiency_model: SufficiencyModel,
        draft_model: DraftModel,
        embedder: Embedder,
        retriever: Any,  # anything with .search(query) -> retrieval dict (C4)
        scorer: ExpertiseScorer,
    ) -> None:
        self._intent = intent_model
        self._sufficiency = sufficiency_model
        self._draft = draft_model
        self._embedder = embedder
        self._retriever = retriever
        self._scorer = scorer

    # -- C1: intent understanding (LLM stub) ------------------------------
    def c1_intent(self, state: dict[str, Any]) -> dict[str, Any]:
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
    def c2_sufficiency(self, state: dict[str, Any]) -> dict[str, Any]:
        intent = IntentResult(
            topics=state.get("topics", []),
            products=state.get("products", []),
            situation=state.get("situation"),
            question_type=state.get("question_type", "製品QA"),
            out_of_scope=state.get("out_of_scope", False),
            confidence=state.get("intent_confidence", 0.0),
        )
        result = self._sufficiency.check(state["question"], intent, state.get("followup_count", 0))
        return {
            "sufficient": result.sufficient,
            "missing": result.missing,
            "followup_question": result.followup_question,
        }

    # -- clarification: pause and ask the user one question ---------------
    def ask(self, state: dict[str, Any]) -> dict[str, Any]:
        reply = interrupt(
            {
                "followup_question": state.get("followup_question"),
                "missing": state.get("missing", []),
            }
        )
        enriched = f"{state['question']} {reply}".strip()
        return {"question": enriched, "followup_count": state.get("followup_count", 0) + 1}

    # -- C3: embed the query ----------------------------------------------
    def c3_embed(self, state: dict[str, Any]) -> dict[str, Any]:
        vector = self._embedder.encode([state["question"]], kind=QUERY)[0]
        return {"query_vector": vector}

    # -- C4: hybrid retrieval ---------------------------------------------
    def c4_retrieve(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"retrieval": self._retriever.search(state["question"])}

    # -- C5: route decision (deterministic) -------------------------------
    def c5_route(self, state: dict[str, Any]) -> dict[str, Any]:
        decision = decide_route(state.get("retrieval") or {})
        return {
            "route": decision.route,
            "route_reason": decision.reason,
            "route_confidence": decision.confidence,
        }

    # -- prior_answer (補助): note who answered before, then hand off ------
    def prior_answer(self, state: dict[str, Any]) -> dict[str, Any]:
        past = (state.get("retrieval") or {}).get("past_answers") or []
        top = past[0] if past else None
        note = (
            f"過去に社員ID {top['responder_id']} が類似の質問に回答しています。"
            if top and top.get("responder_id") is not None
            else "過去の類似回答を参照します。"
        )
        return {"prior_answer_note": note}

    # -- C6: expertise scorer (deterministic) -----------------------------
    def c6_score(self, state: dict[str, Any]) -> dict[str, Any]:
        topics = state.get("topics") or []
        retrieval = state.get("retrieval") or {}
        declined = state.get("declined_ids") or []
        candidates = [p for p in (retrieval.get("candidate_people") or []) if p not in declined]
        if not topics or not candidates:
            return {"recommendations": []}
        asker = state.get("asker")
        asker_id = asker.get("id") if asker else None
        result = self._scorer.rank(topics[0], candidates, asker_id, state["now"], top_k=3)
        return {"recommendations": result["recommendations"]}

    # -- C7: draft the request (LLM stub) ---------------------------------
    def c7_draft(self, state: dict[str, Any]) -> dict[str, Any]:
        top = (state.get("recommendations") or [])[0]
        draft = self._draft.draft(
            state["question"], top, state.get("asker"), state.get("missing") or []
        )
        return {"draft": draft}

    # -- send: pause for the responder's outcome --------------------------
    def send(self, state: dict[str, Any]) -> dict[str, Any]:
        recs = state.get("recommendations") or []
        top = recs[0] if recs else None
        outcome = interrupt({"draft": state.get("draft"), "responder": top})
        return {"outcome": outcome}

    # -- reroute: the top pick declined; try the next candidate -----------
    def reroute(self, state: dict[str, Any]) -> dict[str, Any]:
        recs = state.get("recommendations") or []
        declined = list(state.get("declined_ids") or [])
        if recs:
            declined.append(recs[0]["person_id"])
        return {"declined_ids": declined, "outcome": None, "draft": None, "recommendations": []}

    # -- C8: graph update (minimal, deterministic) ------------------------
    def c8_update(self, state: dict[str, Any]) -> dict[str, Any]:
        # The person_topic_edges online write lands in a later issue; here C8 is a
        # deterministic no-op beyond recording the successful hand-off, so the run
        # stays reproducible.
        recs = state.get("recommendations") or []
        name = recs[0]["name"] if recs else "担当者"
        return {"answer": f"{name}さんに取り次ぎました。回答をお待ちください。"}

    # -- terminals --------------------------------------------------------
    def out_of_scope(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": "恐れ入りますが、こちらは業務の範囲外のご質問のようです。"
            "社内の担当窓口にご確認ください。"
        }

    def document(self, state: dict[str, Any]) -> dict[str, Any]:
        docs = (state.get("retrieval") or {}).get("documents") or []
        top = docs[0] if docs else None
        where = f"（文書ID: {top['doc_id']}）" if top else ""
        return {"answer": f"社内文書に該当がありそうです{where}。該当箇所をご確認ください。"}

    def no_candidate(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": "現時点で適任者が見つかりませんでした。条件を変えて、もう一度お試しください。"
        }
