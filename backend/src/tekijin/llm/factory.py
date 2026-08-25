"""Select the C1/C2/C7 implementations for the configured LLM backend.

``stub`` (default) returns the deterministic, network-free implementations from
:mod:`tekijin.agent.stubs`; ``vllm`` returns the LangChain-backed adapters. The
vLLM import is function-local so the stub path never imports the LangChain
OpenAI client — CI/tests stay LLM-free.
"""

from __future__ import annotations

from tekijin.agent.protocols import (
    AnswerabilityModel,
    DraftModel,
    IntentModel,
    SelfAnswerModel,
    SufficiencyModel,
)
from tekijin.agent.stubs import (
    KeywordIntentModel,
    RuleAnswerabilityModel,
    RuleSufficiencyModel,
    TemplateDraftModel,
    TemplateSelfAnswerModel,
)
from tekijin.config import Settings, get_settings


def make_llm_nodes(
    settings: Settings | None = None,
) -> tuple[IntentModel, SufficiencyModel, DraftModel, AnswerabilityModel, SelfAnswerModel]:
    """Return ``(intent, sufficiency, draft, answerability, self_answer)``.

    The answerability critic (#70) and self-answer composer (#291) are always
    constructed here; whether the graph calls them is gated separately by
    ``answerability_enabled`` / ``self_answer_enabled`` at the service factory, so
    the models are cheap to build and inert until wired.
    """

    settings = settings or get_settings()
    if settings.llm_backend == "vllm":
        from tekijin.llm.vllm import (
            VllmAnswerabilityModel,
            VllmDraftModel,
            VllmIntentModel,
            VllmSelfAnswerModel,
            VllmSufficiencyModel,
        )

        return (
            VllmIntentModel(settings=settings),
            VllmSufficiencyModel(settings=settings),
            VllmDraftModel(settings=settings),
            VllmAnswerabilityModel(settings=settings),
            VllmSelfAnswerModel(settings=settings),
        )
    return (
        KeywordIntentModel(),
        RuleSufficiencyModel(),
        TemplateDraftModel(),
        RuleAnswerabilityModel(),
        TemplateSelfAnswerModel(),
    )
