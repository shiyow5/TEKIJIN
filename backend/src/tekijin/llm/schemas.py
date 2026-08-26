"""Pydantic schemas the LLM (C1/C2) is forced to emit via structured output.

These mirror the dataclasses in :mod:`tekijin.agent.protocols` but carry field
descriptions to steer the model, and are what ``with_structured_output`` binds.
The vLLM adapters convert an instance of these back into the protocol dataclasses.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from tekijin.scorer.topics import TOPIC_VOCABULARY
from tekijin.scorer.weights import BRANCH_VOCABULARY

# The closed topic list, injected into the JSON Schema C1 is generated against so
# guided decoding can only emit these strings (#64). Free-text topics drift from
# the vocabulary the scorer joins on, and an un-joinable topic matches NO evidence
# — the recommendation then goes random (#116).
_TOPIC_ENUM_SCHEMA: dict[str, object] = {
    "type": "string",
    "enum": list(TOPIC_VOCABULARY),
}

# The closed branch list for the #83 location constraint. Nullable: most questions
# name no location, and inventing one would wrongly narrow the candidates. Same
# constrain-generation / forgive-parsing split as the topic enum above.
_BRANCH_ENUM_SCHEMA: dict[str, object] = {
    "anyOf": [{"type": "string", "enum": list(BRANCH_VOCABULARY)}, {"type": "null"}],
}


class IntentSchema(BaseModel):
    """C1 structured output (model-definition §2 C1)."""

    # NB: the annotation stays ``list[str]`` — deliberately NOT ``list[Literal[...]]``.
    # The enum belongs in the schema handed to the model (it constrains GENERATION),
    # but parsing must stay lenient: a backend without guided decoding, or an older
    # stub/fixture, would otherwise raise ValidationError and fail the whole C1 call
    # instead of degrading. ``VllmIntentModel.analyze`` still snaps every value onto
    # the vocabulary via ``normalize_topics``, so a stray topic can never reach the
    # scorer either way (#116). Constrain generation, forgive parsing.
    topics: list[str] = Field(
        default_factory=list,
        description="質問の技術トピック（一覧から該当するものだけを選ぶ／無ければ空配列）",
        json_schema_extra={"items": _TOPIC_ENUM_SCHEMA},
    )
    products: list[str] = Field(default_factory=list, description="言及された製品名")
    situation: str | None = Field(default=None, description="状況の一言要約")
    question_type: str = Field(
        default="製品QA", description="製品QA/見積/技術相談/事務手続き/雑談/業務外 のいずれか"
    )
    out_of_scope: bool = Field(default=False, description="業務外・悪意ある入力なら true")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="意図理解の確信度")
    # #83: an explicitly requested location is a CONDITION to satisfy, not a
    # preference to add points for — C6 treats it as a filter. So it must be
    # extracted only when the asker actually asked for it; see the prompt.
    constraint_branch: str | None = Field(
        default=None,
        description="相談者が明示的に希望した対応者の拠点。希望が無ければ null",
        json_schema_extra=_BRANCH_ENUM_SCHEMA,
    )


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


class CaseExtractionSchema(BaseModel):
    """Knowledge-unit extraction structured output (#357 slice 2, case type).

    The model reads ONE raw record (a sales daily report for the PoC) and, if it
    holds a reusable *case*, distils it into ``問題(状況) → 打ち手 → 結果``. Not every
    record is a case — a status note with no problem/action is not — so
    ``extractable`` lets the model pass (the caller then stores nothing, keeping a
    non-case out of the knowledge base). ``topics`` are NOT emitted here: they come
    from the source record's precomputed tags so the knowledge vocabulary can never
    drift from the eval gold's. The model must ground every field in the supplied
    text and never invent a ``result`` that is not stated (leave it null).
    """

    extractable: bool = Field(
        default=False, description="この記録が再利用可能なケース(課題→打ち手→結果)を含むなら true"
    )
    problem: str = Field(default="", description="顧客の状況・課題（記録に書かれた範囲で）")
    action: str = Field(default="", description="打ち手・提案した商材やソリューション")
    result: str | None = Field(
        default=None, description="結果（受注・継続商談など）。記録に無ければ null"
    )
    industry: str | None = Field(
        default=None, description="顧客の業種（記録に明示があれば）。無ければ null"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="抽出の確信度")

    @model_validator(mode="after")
    def _extractable_requires_problem_and_action(self) -> CaseExtractionSchema:
        # A case is meaningless without BOTH a problem and an action — if the model
        # claims extractable it must supply both, else the caller would store an
        # empty unit. (extractable=false with stray text is harmless: skipped.)
        if self.extractable:
            if not self.problem.strip():
                raise ValueError("problem is required when extractable is true")
            if not self.action.strip():
                raise ValueError("action is required when extractable is true")
        return self


class SelfAnswerSchema(BaseModel):
    """Self-answer structured output (#291): a grounded, cited answer or a pass.

    The model must answer ONLY from the supplied evidence and list the source ids it
    used. When the evidence does not answer the question it sets ``grounded=false``
    (and leaves ``answer`` empty) so the graph falls back to a human hand-off rather
    than surfacing an ungrounded answer.
    """

    grounded: bool = Field(
        default=False, description="提供された根拠だけで質問に回答できるなら true"
    )
    answer: str = Field(default="", description="根拠のみに基づく回答本文（grounded=false なら空）")
    cited_source_ids: list[str] = Field(
        default_factory=list, description="回答に実際に用いた根拠の source_id（提供分の部分集合）"
    )

    @model_validator(mode="after")
    def _grounded_requires_answer_and_citation(self) -> SelfAnswerSchema:
        # A grounded answer must carry BOTH text and at least one citation — else the
        # graph would emit an empty/uncited "answer" instead of falling back to
        # routing. A grounded answer with zero citations is the strongest signal the
        # model fabricated it (the composer verifies the ids are real; here we at
        # least require it to claim one). (The reverse — grounded=false with stray
        # text — is harmless: the graph ignores it and routes.)
        if self.grounded:
            if not self.answer.strip():
                raise ValueError("answer is required when grounded is true")
            if not self.cited_source_ids:
                raise ValueError("cited_source_ids is required when grounded is true")
        return self
