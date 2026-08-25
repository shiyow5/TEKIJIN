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
    SufficiencyModel,
)
from tekijin.agent.stubs import (
    KeywordIntentModel,
    RuleAnswerabilityModel,
    RuleSufficiencyModel,
    TemplateDraftModel,
)
from tekijin.config import Settings, get_settings


def make_llm_nodes(
    settings: Settings | None = None,
) -> tuple[IntentModel, SufficiencyModel, DraftModel, AnswerabilityModel]:
    """Return ``(intent, sufficiency, draft, answerability)`` for the backend.

    The answerability critic (#70) is always constructed here; whether the graph
    actually calls it is gated separately by ``answerability_enabled`` at the
    service factory, so the model is cheap to build and inert until wired.
    """

    settings = settings or get_settings()
    if settings.llm_backend == "vllm":
        from tekijin.llm.vllm import (
            VllmAnswerabilityModel,
            VllmDraftModel,
            VllmIntentModel,
            VllmSufficiencyModel,
        )

        return (
            VllmIntentModel(settings=settings),
            VllmSufficiencyModel(settings=settings),
            VllmDraftModel(settings=settings),
            VllmAnswerabilityModel(settings=settings),
        )
    return (
        KeywordIntentModel(),
        RuleSufficiencyModel(),
        TemplateDraftModel(),
        RuleAnswerabilityModel(),
    )
