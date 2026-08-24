"""Deterministic, prompt-independent safety net for C1 (#155).

C1's ``out_of_scope`` judgement is prompt-dependent (#118): a model swap or a new
adversarial phrasing could regress it with no warning. :func:`scan_disallowed` adds
a STRUCTURAL layer that runs regardless of which intent model is wired, forcing
rejection of two clear classes:

* **prompt injection** — known override / role-impersonation phrases (single-factor:
  the phrase alone is enough).
* **PII / secret solicitation** — deliberately gated on a "whose data" signal so it
  does NOT swallow ordinary self-service helpdesk traffic (the very "product QA" this
  agent routes). Two rules, each needing a possession/scope marker (さん/他人/全員/…):

  - *others' personal data* (住所・給与・人事評価・健康診断・個人情報 …) requested
    ABOUT someone else / everyone, with a solicitation verb.
  - *credentials / confidential material* (パスワード・接続情報・機密文書 …) requested
    with a solicitation verb AND an others'/exfiltration marker (他人/全員/本番/社外 …).

Because both rules require that possession/scope marker, asking how to reset *your
own* password or book *your own* health checkup is NOT caught — only third-party or
aggregate/exfiltration requests are.

This is a NET, not a replacement for C1: it only ADDS rejections; softer out-of-scope
cases (small talk, one's own leave balance, no available expert) stay the model's /
C2's / C5's call. It is a substring matcher over NFKC-normalized, WHITESPACE-STRIPPED
text (#172), so space insertion inside a phrase ("年収を教え　て") no longer bypasses
it; synonyms outside the lists, romaji, or NON-whitespace characters inserted inside a
phrase can still slip past (documented limitation; those are a further follow-up).
"""

from __future__ import annotations

import re
import unicodedata

# Any run of Unicode whitespace, removed before matching so spacing can't split a
# target/verb ("年収を教え て"). NFKC (applied first) has already folded the full-width
# space U+3000 to a plain space, so ``\s`` covers it too.
_WHITESPACE = re.compile(r"\s+")

# Known prompt-injection / role-impersonation markers. Matching any ONE is enough.
# English phrases are kept SPECIFIC (no bare "act as" / "disregard the") so ordinary
# technical prose ("configured to act as a gateway") does not false-positive.
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
    "system prompt",
    "you are now",
)

# Others' personal data. Caught only WITH a possession/scope marker + solicitation.
_PERSONAL_TARGETS: tuple[str, ...] = (
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
    "個人情報",
)

# Credentials / connection secrets / confidential material. Caught only WITH a
# solicitation verb AND an others'/exfiltration marker — NOT on self-service phrasing
# like "パスワードの設定方法を教えて".
_SECRET_TARGETS: tuple[str, ...] = (
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

# "Whose data" markers: a third party, or an aggregate/roster over people.
_POSSESSION_MARKERS: tuple[str, ...] = (
    "さん",
    "様",
    "氏",
    "くん",
    "他人",
    "他の人",
    "別の人",
    "誰か",
    "誰の",
    "全員",
    "全社員",
    "名簿",
    "一覧",
    "リスト",
    "みんな",
)

# Exfiltration context that makes a secret request dangerous even without a person.
_EXFIL_MARKERS: tuple[str, ...] = ("本番", "社外", "外部")

# Solicitation / aggregation verbs that turn a mention into a request for data.
_SOLICITATION: tuple[str, ...] = (
    "教えて",
    "おしえて",
    "一覧",
    "リスト",
    "名簿",
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
    "送って",
    "渡して",
)

PROMPT_INJECTION = "prompt_injection"
PII_OR_SECRET = "pii_or_secret_solicitation"


def _compact(text: str) -> str:
    """Remove all whitespace so spacing inside a phrase cannot dodge a match (#172)."""

    return _WHITESPACE.sub("", text)


def _has_any(haystack_compact: str, needles: tuple[str, ...]) -> bool:
    # Both sides are whitespace-stripped, so "ignore  previous" / "年収を教え て" still
    # match "ignore previous" / "年収…教え". Needles are lower-cased to match ``low``.
    return any(_compact(n.lower()) in haystack_compact for n in needles)


def scan_disallowed(text: str | None) -> str | None:
    """Return a rejection reason for disallowed content, or ``None`` if clean.

    Reasons: ``"prompt_injection"`` or ``"pii_or_secret_solicitation"``. Text is
    NFKC-normalized, lower-cased, and WHITESPACE-STRIPPED before matching (folds
    full-width tricks and space-insertion bypasses, #172); markers are substrings.
    """

    if not text:
        return None
    compact = _compact(unicodedata.normalize("NFKC", text).lower())

    if _has_any(compact, _INJECTION_PATTERNS):
        return PROMPT_INJECTION

    has_solicitation = _has_any(compact, _SOLICITATION)
    if not has_solicitation:
        return None
    has_possession = _has_any(compact, _POSSESSION_MARKERS)

    # Rule A: others' personal data (needs a possession/scope marker).
    if has_possession and _has_any(compact, _PERSONAL_TARGETS):
        return PII_OR_SECRET

    # Rule B: credentials/confidential (needs an others'/exfiltration marker), so
    # self-service phrasing ("自分のパスワードの再設定方法") is left alone.
    if (has_possession or _has_any(compact, _EXFIL_MARKERS)) and _has_any(compact, _SECRET_TARGETS):
        return PII_OR_SECRET

    return None
