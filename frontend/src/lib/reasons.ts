/**
 * Labels and helpers for recommendation `reasons` (the "why this person" chips).
 *
 * Reason `type` values come from the scorer (model-definition): cert / answers /
 * project / load / proximity / recency / skill / self. `skill` is an *inferred*
 * skill ("推定スキル: …"), distinct from `self`, the person's *self-declared*
 * skill ("自己申告スキル: …"). Unknown types fall back to the raw string so a new
 * scorer signal still renders.
 */

export const REASON_LABELS: Record<string, string> = {
  cert: "関連資格",
  answers: "過去回答",
  project: "類似案件担当",
  load: "現在の負荷",
  proximity: "距離の近さ",
  recency: "直近の活動",
  skill: "推定スキル",
  self: "自己申告",
};

export function reasonLabel(type: string): string {
  return REASON_LABELS[type] ?? type;
}
