/**
 * Labels and helpers for recommendation `reasons` (the "why this person" chips).
 *
 * Reason `type` values come from the scorer (model-definition): cert / answers /
 * project / load / proximity / recency / self. Unknown types fall back to the
 * raw string so a new scorer signal still renders.
 */

import type { Reason } from "@/lib/api-types";

export const REASON_LABELS: Record<string, string> = {
  cert: "関連資格",
  answers: "過去回答",
  project: "類似案件担当",
  load: "現在の負荷",
  proximity: "距離の近さ",
  recency: "直近の活動",
  self: "得意分野",
};

export function reasonLabel(type: string): string {
  return REASON_LABELS[type] ?? type;
}

/**
 * Best-effort reuse/answer count from the `answers` reason detail (e.g.
 * "過去回答: 45件" -> 45). Returns `null` when there is no such reason or no
 * number in it, so the caller can omit the "N人に役立ちました" line.
 */
export function parseReuseCount(reasons: readonly Reason[]): number | null {
  const answers = reasons.find((r) => r.type === "answers");
  if (!answers) {
    return null;
  }
  const match = answers.detail.match(/\d+/);
  return match ? Number(match[0]) : null;
}
