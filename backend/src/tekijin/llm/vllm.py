"""vLLM-backed C1/C2/C7 implementations (LangChain OpenAI-compatible client).

These satisfy the :mod:`tekijin.agent.protocols` interfaces using a chat model
built with ``init_chat_model("openai:<model>", base_url=…, api_key=…)`` against
the vLLM ``/v1`` endpoint. ``langchain``/``langchain_openai`` are imported LAZILY
inside ``_build_*`` so importing this module (or running the API on the ``stub``
backend) never pulls them — CI stays LLM-free.

The bound runnable is dependency-injected: pass ``model=…`` (a
``.with_structured_output``-bound runnable for C1/C2, or a chat model for C7) to
unit-test the prompt/parse logic with a fake; leave it ``None`` in production to
lazily build the real network client.
"""

from __future__ import annotations

from typing import Any

from tekijin.agent.protocols import IntentResult, SufficiencyResult
from tekijin.config import Settings, get_settings
from tekijin.llm.schemas import IntentSchema, SufficiencySchema

_INTENT_SYSTEM = (
    "あなたは社内Q&Aの意図解析器です。質問を、検索とスコアリングで使える構造に"
    "落としてください。業務外・悪意ある入力は out_of_scope=true にします。"
)
_SUFFICIENCY_SYSTEM = (
    "あなたは情報充足の点検器です。取り次ぐ前に判断へ必要な情報が揃っているかを"
    "確認し、足りなければ『まとめて1つ』の逆質問を返します。"
)
_DRAFT_SYSTEM = (
    "あなたは依頼文の作成者です。相手の職種・関係性に合わせ、必須項目が埋まった"
    "失礼のない依頼文を、敬体で簡潔に作成してください。事実を創作しないこと。"
)


def _openai_model(name: str, settings: Settings) -> Any:  # pragma: no cover - network client
    from langchain.chat_models import init_chat_model

    # Qwen3 is a reasoning model; unless we opt in, tell vLLM to skip the <think>
    # pass via the chat template. Thinking-ON made the forced tool-call structured
    # outputs slow and occasionally empty (see Settings.llm_enable_thinking / #116).
    return init_chat_model(
        f"openai:{name}",
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        extra_body={"chat_template_kwargs": {"enable_thinking": settings.llm_enable_thinking}},
    )


class VllmIntentModel:
    """C1: structured intent extraction over vLLM."""

    def __init__(self, *, model: Any | None = None, settings: Settings | None = None) -> None:
        self._model = model
        self._settings = settings or get_settings()

    def _structured(self) -> Any:  # pragma: no cover - builds a network client
        return _openai_model(self._settings.llm_model, self._settings).with_structured_output(
            IntentSchema
        )

    @staticmethod
    def prompt(question: str, asker: dict[str, Any] | None) -> list[tuple[str, str]]:
        who = f"（依頼者: {asker}）" if asker else ""
        return [("system", _INTENT_SYSTEM), ("human", f"質問{who}: {question}")]

    def analyze(self, question: str, asker: dict[str, Any] | None) -> IntentResult:
        model = self._model if self._model is not None else self._structured()
        out: IntentSchema | None = model.invoke(self.prompt(question, asker))
        if out is None:  # forced tool call was not emitted (e.g. reasoning suppressed it)
            raise ValueError("C1 intent: structured output was empty (no tool call from the LLM)")
        return IntentResult(
            topics=list(out.topics),
            products=list(out.products),
            situation=out.situation,
            question_type=out.question_type,
            out_of_scope=out.out_of_scope,
            confidence=out.confidence,
        )


class VllmSufficiencyModel:
    """C2: structured sufficiency / follow-up over vLLM."""

    def __init__(self, *, model: Any | None = None, settings: Settings | None = None) -> None:
        self._model = model
        self._settings = settings or get_settings()

    def _structured(self) -> Any:  # pragma: no cover - builds a network client
        return _openai_model(self._settings.llm_model, self._settings).with_structured_output(
            SufficiencySchema
        )

    @staticmethod
    def prompt(question: str, intent: IntentResult) -> list[tuple[str, str]]:
        context = f"トピック={intent.topics} 製品={intent.products} 種別={intent.question_type}"
        return [("system", _SUFFICIENCY_SYSTEM), ("human", f"{context}\n質問: {question}")]

    def check(self, question: str, intent: IntentResult, followup_count: int) -> SufficiencyResult:
        model = self._model if self._model is not None else self._structured()
        out: SufficiencySchema | None = model.invoke(self.prompt(question, intent))
        if out is None:  # forced tool call was not emitted (e.g. reasoning suppressed it)
            raise ValueError(
                "C2 sufficiency: structured output was empty (no tool call from the LLM)"
            )
        return SufficiencyResult(
            sufficient=out.sufficient,
            missing=list(out.missing),
            followup_question=out.followup_question,
        )


class VllmDraftModel:
    """C7: free-text hand-off draft over vLLM."""

    def __init__(self, *, model: Any | None = None, settings: Settings | None = None) -> None:
        self._model = model
        self._settings = settings or get_settings()

    def _chat(self) -> Any:  # pragma: no cover - builds a network client
        return _openai_model(self._settings.llm_model, self._settings)

    @staticmethod
    def prompt(
        question: str,
        responder: dict[str, Any],
        asker: dict[str, Any] | None,
        missing: list[str],
    ) -> list[tuple[str, str]]:
        gaps = f"未確認: {', '.join(missing)}" if missing else "未確認項目なし"
        human = f"相手={responder} 依頼者={asker}\n相談内容: {question}\n{gaps}"
        return [("system", _DRAFT_SYSTEM), ("human", human)]

    def draft(
        self,
        question: str,
        responder: dict[str, Any],
        asker: dict[str, Any] | None,
        missing: list[str],
    ) -> str:
        model = self._model if self._model is not None else self._chat()
        response = model.invoke(self.prompt(question, responder, asker, missing))
        content = getattr(response, "content", response)
        return content if isinstance(content, str) else str(content)
