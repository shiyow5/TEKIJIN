"""Deterministic, LLM-free stand-ins for the C1/C2/C7 LLM nodes.

These satisfy the protocols in :mod:`tekijin.agent.protocols` with rule-based
logic — keyword tables and templates — so the whole graph runs (and is tested)
without a model or the network. They are the DEFAULT implementations; a real
vLLM-backed version is injected later without changing the graph. Every method
is a pure function of its inputs, so runs are reproducible.
"""

from __future__ import annotations

from typing import Any

from tekijin.agent.protocols import IntentResult, SufficiencyResult

# At most one clarifying round (model-definition C2: "逆質問はまとめて1回").
MAX_FOLLOWUPS = 1

# Below this C1 confidence — or with no topic extracted at all — the intent is
# too unclear to search on, so C2 asks the user to clarify (model-definition C1:
# "confidence<閾値 → C2 の逆質問へ").
INTENT_CONFIDENCE_THRESHOLD = 0.5

# Question keyword -> canonical topic (matched case-insensitively). Extend freely;
# this is data, not logic.
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ネットワーク・VPN": ("vpn", "ネットワーク", "回線", "拠点間"),
    "セキュリティ": ("セキュリティ", "utm", "ファイアウォール", "脆弱性", "不正アクセス"),
    "クラウド移行": ("クラウド", "aws", "オンプレ", "移行"),
    "サーバー・インフラ運用": ("サーバー", "インフラ", "linux", "運用監視"),
    "システム開発・API": ("api", "システム開発", "連携"),
    "データ基盤・分析": ("データ基盤", "分析", "bi", "データ活用"),
    "CRM・営業支援": ("crm", "営業支援", "顧客管理"),
    "Webマーケティング・広告": ("広告", "webマーケ", "seo"),
    "人事・採用": ("採用", "人事", "労務"),
    "経理・決算": ("経理", "決算", "簿記", "仕訳"),
    "契約管理": ("契約",),
    "問い合わせ・ヘルプデスク運用": ("ヘルプデスク", "問い合わせ対応"),
}

# Product-like tokens to surface verbatim when present (case-insensitive match).
PRODUCT_KEYWORDS: tuple[str, ...] = ("UTM", "VPN", "CRM", "API", "BI", "EC", "SNS")

# Anything clearly not work-related -> out_of_scope.
OUT_OF_SCOPE_KEYWORDS: tuple[str, ...] = (
    "天気",
    "ランチ",
    "昼飯",
    "株価",
    "競馬",
    "恋愛",
    "芸能",
    "プライベート",
    "旅行",
)

# question_type keyword tables (checked in order after out_of_scope).
_QUOTE_KEYWORDS = ("見積", "価格", "費用", "料金")
_ADMIN_KEYWORDS = ("申請", "手続き", "経費精算", "精算")
_CHITCHAT_KEYWORDS = ("こんにちは", "雑談", "おはよう", "よろしく")

# Required information slots per question type, and how to detect each in text.
_REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "見積": ("現行製品", "対象拠点数"),
    "技術相談": ("現行製品", "対象拠点数"),
}


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


class KeywordIntentModel:
    """C1 stub: extract topics/products and classify by keyword tables."""

    def analyze(self, question: str, asker: dict[str, Any] | None) -> IntentResult:
        lowered = question.lower()

        out_of_scope = _contains_any(question, OUT_OF_SCOPE_KEYWORDS)
        topics = [t for t, kws in TOPIC_KEYWORDS.items() if _contains_any(lowered, kws)]
        products = [p for p in PRODUCT_KEYWORDS if p.lower() in lowered]
        question_type = self._classify(question, topics, products, out_of_scope)

        if out_of_scope:
            confidence = 0.9  # confident it is off-topic
        else:
            confidence = 0.4
            if topics:
                confidence += 0.25
            if products:
                confidence += 0.15
            if question_type in ("見積", "技術相談", "事務手続き"):
                confidence += 0.1
        confidence = round(min(confidence, 1.0), 2)

        return IntentResult(
            topics=topics,
            products=products,
            situation=None,
            question_type=question_type,
            out_of_scope=out_of_scope,
            confidence=confidence,
        )

    @staticmethod
    def _classify(question: str, topics: list[str], products: list[str], out_of_scope: bool) -> str:
        if out_of_scope:
            return "業務外"
        if _contains_any(question, _QUOTE_KEYWORDS):
            return "見積"
        if _contains_any(question, _ADMIN_KEYWORDS):
            return "事務手続き"
        if _contains_any(question, _CHITCHAT_KEYWORDS):
            return "雑談"
        if topics or products:
            return "技術相談"
        return "製品QA"


class RuleSufficiencyModel:
    """C2 stub: flag missing required slots; ask back at most once."""

    def check(self, question: str, intent: IntentResult, followup_count: int) -> SufficiencyResult:
        required = _REQUIRED_SLOTS.get(intent.question_type, ())
        missing = [slot for slot in required if not self._slot_present(slot, question, intent)]

        # Already asked our one clarification -> proceed regardless, but KEEP any
        # still-unresolved slots so C7 can flag them (do not silently clear them).
        if followup_count >= MAX_FOLLOWUPS:
            return SufficiencyResult(sufficient=True, missing=missing, followup_question=None)

        # Intent itself too weak to search on -> ask to clarify the intent.
        if intent.confidence < INTENT_CONFIDENCE_THRESHOLD or not intent.topics:
            return SufficiencyResult(
                sufficient=False,
                missing=["相談内容"],
                followup_question="ご相談の内容やトピックを、もう少し具体的に教えてください。",
            )
        if not missing:
            return SufficiencyResult(sufficient=True, missing=[], followup_question=None)
        followup = "次の点を教えてください: " + "、".join(missing)
        return SufficiencyResult(sufficient=False, missing=missing, followup_question=followup)

    @staticmethod
    def _slot_present(slot: str, question: str, intent: IntentResult) -> bool:
        if slot == "現行製品":
            return bool(intent.products) or _contains_any(question, ("現行", "既存", "今使"))
        if slot == "対象拠点数":  # pragma: no branch - only two slots defined
            return "拠点" in question
        return True  # pragma: no cover - defensive; unknown slots count as present


class TemplateDraftModel:
    """C7 stub: fill a polite Japanese hand-off template (敬体, 簡潔)."""

    def draft(
        self,
        question: str,
        responder: dict[str, Any],
        asker: dict[str, Any] | None,
        missing: list[str],
    ) -> str:
        name = responder.get("name") or "ご担当者"
        dept = responder.get("dept") or responder.get("department") or ""
        header = f"{name}さん（{dept}）" if dept else f"{name}さん"
        lines = [
            header,
            "お世話になっております。下記の件でご相談させてください。",
            f"【相談内容】\n{question}",
        ]
        if missing:
            lines.append("【補足いただきたい点】\n" + "、".join(missing))
        lines.append("お手数ですが、ご確認いただけますと幸いです。")
        return "\n".join(lines)
