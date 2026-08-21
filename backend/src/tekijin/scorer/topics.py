"""Topic matching for evidence sources that lack a clean topic field.

``skills.topic`` and ``answers.topic`` already equal a question topic verbatim,
so they need no mapping. Certifications (free-text names) and projects (a
``product`` label) do. These lookup tables make that correspondence explicit,
deterministic, and tunable — like the weights, they are data, not logic, and can
be extended or overridden without changing the scorer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# Certification name -> topics it is evidence for, matched by substring so name
# variants ("...スペシャリスト", suffixes) still resolve. Topics absent here simply
# receive no certification evidence (skills / answers / projects still apply).
CERT_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ネットワーク・VPN": ("ネットワーク",),
    "セキュリティ": ("セキュリティ", "安全確保支援士"),
    "データ基盤・分析": ("データベース", "E資格", "G検定", "データ"),
    "クラウド移行": ("AWS", "クラウド"),
    "サーバー・インフラ運用": ("LPIC", "Linux", "インフラ"),
    "システム開発・API": ("基本情報", "応用情報"),
    "契約管理": ("法務",),
    "人事・採用": ("社会保険労務士", "給与計算", "衛生管理"),
    "経理・決算": ("簿記",),
    "CRM・営業支援": ("販売士", "中小企業診断士"),
}

# Project ``product`` -> the single question topic it evidences.
PRODUCT_TOPIC_MAP: dict[str, str] = {
    "CRM導入支援": "CRM・営業支援",
    "ECサイト構築": "ECサイト構築",
    "SNS運用代行": "SNS運用",
    "Webマーケティング支援": "Webマーケティング・広告",
    "広告運用代行": "Webマーケティング・広告",
    "クラウド移行支援": "クラウド移行",
    "データ基盤構築": "データ基盤・分析",
    "パフォーマンスチューニング": "パフォーマンスチューニング",
    "ヘルプデスク運用代行": "問い合わせ・ヘルプデスク運用",
    "問い合わせ対応システム導入": "問い合わせ・ヘルプデスク運用",
    "モバイルアプリ開発": "モバイルアプリ開発",
    "保守運用サポート": "サーバー・インフラ運用",
    "基幹システム導入": "基幹システム",
    "契約管理システム導入": "契約管理",
    "業務効率化コンサルティング": "業務効率化コンサル",
}


def cert_matches_topic(
    cert_name: str | None,
    topic: str,
    keyword_map: Mapping[str, Sequence[str]] = CERT_TOPIC_KEYWORDS,
) -> bool:
    """True when ``cert_name`` contains any keyword mapped to ``topic``."""

    if not cert_name:
        return False
    return any(keyword in cert_name for keyword in keyword_map.get(topic, ()))


def product_matches_topic(
    product: str | None,
    topic: str,
    product_map: Mapping[str, str] = PRODUCT_TOPIC_MAP,
) -> bool:
    """True when a project's ``product`` maps to ``topic``."""

    if not product:
        return False
    return product_map.get(product) == topic
