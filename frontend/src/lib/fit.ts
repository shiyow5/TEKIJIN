/**
 * Fit-gauge magnitudes for recommendation cards (#222).
 *
 * The qualitative confidence label (高/中/低) saturates easily — several strong
 * candidates all reach 高, so a gauge driven purely by the label shows every card
 * at 100% and stops discriminating between them (#222). To differentiate the
 * *shown* candidates, we anchor the top card to its own confidence ceiling and
 * scale the rest by their score ratio to the top. The raw internal `score` is
 * never displayed — only this relative percentage derived from it.
 */

/** Fraction of the ring each qualitative level fills; unknown → half (neutral). */
export const LEVEL_FRACTION: Record<string, number> = { 高: 1, 中: 0.66, 低: 0.33 };

export function levelFraction(level: string): number {
  return LEVEL_FRACTION[level] ?? 0.5;
}

export interface FitInput {
  score: number;
  confidence: string;
}

/**
 * Relative fit percentages for a score-desc list of candidates.
 *
 * The top candidate is anchored to its confidence level's ceiling (高→100, 中→66,
 * 低→33), so an absolutely-weak top pick is not overstated as a perfect match;
 * lower candidates are scaled down by their score ratio to the top, so equally
 * "高" people still separate. When the top score is not positive (nothing to
 * normalise against), each card falls back to its own level percentage.
 */
export function relativeFitPercents(candidates: readonly FitInput[]): number[] {
  if (candidates.length === 0) return [];
  const topScore = candidates[0].score;
  const anchor = levelFraction(candidates[0].confidence);
  return candidates.map((c) => {
    if (topScore > 0) {
      const ratio = Math.max(0, Math.min(c.score, topScore)) / topScore;
      return Math.round(anchor * 100 * ratio);
    }
    return Math.round(levelFraction(c.confidence) * 100);
  });
}
