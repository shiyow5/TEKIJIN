"""Tunable constants for the C6 expertise scorer.

Every magic number the scorer depends on lives here, frozen and documented, so
the score is reproducible and the weights can be swapped without touching logic
(technical-spec §5: "重みは評価セットで調整する。手で決めた値をそのまま出さない").
Pass a different :class:`Weights` to :class:`~tekijin.scorer.scorer.ExpertiseScorer`
to override the defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Weights:
    """Linear weights for ``score = w1·topic_fit + w2·recency + w3·answer_quality
    + w4·proximity − w5·load``.

    Defaults: topic_fit dominates (it is the "who actually knows this" signal);
    answer_quality is the next strongest (proven, helpful answers); recency and
    proximity are supporting nudges; load is a middling penalty — strong enough
    to spread work (risk ②, 負荷集中) yet never enough to bury a true expert.
    """

    topic_fit: float = 0.45
    recency: float = 0.15
    answer_quality: float = 0.20
    proximity: float = 0.10
    load: float = 0.20


DEFAULT_WEIGHTS = Weights()

# Evidence base_scores (doc15 / db-schema.md): helpful answer 1.0 > project lead
# 0.8 > past answer 0.7 > certification 0.6 > project member 0.5 > self-declared
# skill 0.3. These are summed per (person, topic) and saturated into an edge
# weight; a decline is NOT negative evidence (it lowers availability, not skill).
BASE_SCORE_HELPFUL_ANSWER = 1.0
BASE_SCORE_PROJECT_LEAD = 0.8
BASE_SCORE_ANSWER = 0.7
BASE_SCORE_CERTIFICATION = 0.6
BASE_SCORE_PROJECT_MEMBER = 0.5
BASE_SCORE_SKILL = 0.3

# Half-life of experience relevance: a 6-month-old project/answer counts half as
# much toward ``recency`` (technical-spec §5: 半減期6か月の時間減衰).
RECENCY_HALF_LIFE_DAYS = 182.5

# Evidence source_types that decay with time and so feed ``recency``. A
# certification does not become less true as it ages, so it is deliberately
# excluded (technical-spec §5 ties recency to projects/answers).
RECENCY_SOURCE_TYPES = ("project", "answer")

# ``load`` saturates: this many recent items (recommendations + answers in the
# window) map to a 0.5 penalty; more approaches, but never reaches, 1.0.
LOAD_HALF_SATURATION = 5.0

# ``answer_quality`` saturation scale (larger raw quality -> closer to 1.0).
ANSWER_QUALITY_SCALE = 3.0

# ``answer_quality`` raw-score coefficients: a helpful answer counts most, reuse
# adds, and simply having answered gives a floor (all summed, then saturated).
ANSWER_QUALITY_ANSWER_COEF = 0.3
ANSWER_QUALITY_HELPFUL_COEF = 0.7
ANSWER_QUALITY_REUSE_COEF = 0.2

# Approximate days per month, for the human-readable "約Nか月前" recency reason.
DAYS_PER_MONTH = 30.4

# The ``load`` window: how many days back "recent" reaches (technical-spec §5:
# 直近7日).
LOAD_WINDOW_DAYS = 7

# Proximity tiers (technical-spec §5: 同支店 > 同エリア > 全社).
PROXIMITY_SAME_BRANCH = 1.0
PROXIMITY_SAME_REGION = 0.5
PROXIMITY_COMPANY_WIDE = 0.2

# Region grouping for the middle proximity tier. Branches are cities; this maps
# each to a coarse region so "same area" sits between "same branch" and
# "company-wide". Extend as branches are added.
REGION_OF_BRANCH: dict[str, str] = {
    "本社": "関東",
    "東京": "関東",
    "名古屋": "中部",
    "大阪": "関西",
    "福岡": "九州",
}
