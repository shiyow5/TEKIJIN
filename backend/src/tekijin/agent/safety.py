"""Deterministic, prompt-independent safety net for C1 (#155).

C1's ``out_of_scope`` judgement is prompt-dependent (#118): a model swap or a new
adversarial phrasing could regress it with no warning. :func:`scan_disallowed` adds
a STRUCTURAL layer that runs regardless of which intent model is wired, forcing
rejection of two clear classes:

* **prompt injection** — known override / role-impersonation phrases (single-factor:
  the phrase alone is enough).
* **PII / secret solicitation** — a sensitive target (others' personal data, or
  credentials / connection secrets) requested with a solicitation verb (two-factor:
  target AND solicitation, to keep false positives down — merely *mentioning*
  "パスワード" while asking how to reset one's own is not caught).

This is a NET, not a replacement for C1: it only ADDS rejections. Softer
out-of-scope cases (small talk, one's own leave balance, no available expert) are
left to C1/C2/C5. The net errs toward safety, so a legitimate question that pairs a
sensitive target with a solicitation verb may be refused — an accepted trade for a
prototype whose priority is "回答の出所は常に人" and no data exfiltration.
"""

from __future__ import annotations

# Known prompt-injection / role-impersonation markers. Matching any ONE is enough.
_INJECTION_PATTERNS: tuple[str, ...] = (
    "これまでの指示",
    "指示を無視",
    "指示は無視",
    "命令を無視",
    "命令を忘れ",
    "指示を忘れ",
    "システム管理者",
    "あなたは管理者",
    "管理者として",
    "管理者です",
    "システムとして振る舞",
    "権限を昇格",
    "jailbreak",
    "ignore previous",
    "ignore above",
    "ignore all previous",
    "disregard previous",
    "disregard the",
    "system prompt",
    "you are now",
    "act as",
)

# Sensitive targets: others' personal data, plus credentials / connection secrets.
# Deliberately EXCLUDES the asker's own soft-PII (e.g. 有給/有休) — that is a C1
# out_of_scope case, not exfiltration, and would over-reject if netted here.
_SENSITIVE_TARGETS: tuple[str, ...] = (
    "住所",
    "電話番号",
    "携帯番号",
    "給与",
    "給料",
    "年収",
    "人事評価",
    "査定",
    "健康診断",
    "病歴",
    "マイナンバー",
    "口座番号",
    "銀行口座",
    "メールアドレス",
    "退職予定",
    "退職者",
    # credentials / connection secrets
    "パスワード",
    "apiキー",
    "api key",
    "秘密鍵",
    "アクセストークン",
    "接続情報",
    "認証情報",
    "クレデンシャル",
    "機密文書",
    "機密情報",
)

# Solicitation / aggregation verbs that turn a mention into a request for data.
_SOLICITATION: tuple[str, ...] = (
    "教えて",
    "おしえて",
    "一覧",
    "リスト",
    "全員",
    "全社員",
    "開示",
    "まとめて",
    "知りたい",
    "出して",
    "出力",
    "抽出",
    "ダウンロード",
    "コピー",
    "共有",
    "見せて",
    "見せられ",
    "欲しい",
    "ほしい",
    "貼って",
)

PROMPT_INJECTION = "prompt_injection"
PII_OR_SECRET = "pii_or_secret_solicitation"


def scan_disallowed(text: str | None) -> str | None:
    """Return a rejection reason for disallowed content, or ``None`` if clean.

    Reasons: ``"prompt_injection"`` or ``"pii_or_secret_solicitation"``. Matching is
    case-insensitive for ASCII markers; Japanese markers are matched as substrings.
    """

    if not text:
        return None
    low = text.lower()

    for pattern in _INJECTION_PATTERNS:
        if pattern.lower() in low:
            return PROMPT_INJECTION

    has_target = any(target.lower() in low for target in _SENSITIVE_TARGETS)
    if has_target and any(verb.lower() in low for verb in _SOLICITATION):
        return PII_OR_SECRET

    return None
