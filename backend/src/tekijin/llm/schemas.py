"""Pydantic schemas the LLM (C1/C2) is forced to emit via structured output.

These mirror the dataclasses in :mod:`tekijin.agent.protocols` but carry field
descriptions to steer the model, and are what ``with_structured_output`` binds.
The vLLM adapters convert an instance of these back into the protocol dataclasses.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class IntentSchema(BaseModel):
    """C1 structured output (model-definition §2 C1)."""

    topics: list[str] = Field(default_factory=list, description="質問の技術トピック")
    products: list[str] = Field(default_factory=list, description="言及された製品名")
    situation: str | None = Field(default=None, description="状況の一言要約")
    question_type: str = Field(
        default="製品QA", description="製品QA/見積/技術相談/事務手続き/雑談/業務外 のいずれか"
    )
    out_of_scope: bool = Field(default=False, description="業務外・悪意ある入力なら true")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="意図理解の確信度")


class SufficiencySchema(BaseModel):
    """C2 structured output (model-definition §2 C2)."""

    sufficient: bool = Field(default=True, description="取り次ぎ・検索に十分な情報があるか")
    missing: list[str] = Field(default_factory=list, description="不足している必須項目")
    followup_question: str | None = Field(
        default=None, description="不足時に返すまとめて1つの逆質問"
    )

    @model_validator(mode="after")
    def _followup_required_when_insufficient(self) -> SufficiencySchema:
        # An insufficient result MUST carry a non-empty follow-up, or the graph
        # would pause on an empty clarification (the LLM must not omit it).
        if not self.sufficient and not (self.followup_question or "").strip():
            raise ValueError("followup_question is required when sufficient is false")
        return self


class AnswerabilitySchema(BaseModel):
    """Evidence-sufficiency structured output (#70 critic).

    ``confidence`` is deliberately an INTEGER 0–100, not a boolean: asked as a
    yes/no the model over-rejects (18/45 vs 3/45 misreject as a number — #65/#67
    §6). The graph compares it to an externalised threshold.
    """

    confidence: int = Field(
        default=0, ge=0, le=100, description="社内の実績でこの相談に答えられる確度(0-100)"
    )
    reason: str | None = Field(default=None, description="判断の根拠を一言で（任意）")
