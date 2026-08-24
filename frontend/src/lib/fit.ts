/**
 * Fit-gauge magnitudes for recommendation cards (#222 / recalibrated #240).
 *
 * The gauge shows **適合度 (fit strength)**, derived from the candidate's composite
 * score — NOT the qualitative 高/中/低 confidence label. The two are deliberately
 * decoupled (#240): after #110 the label reflects the *kind of evidence* (a topic
 * with no prior answers is 低 even for a strong candidate), so anchoring the gauge
 * to the label made a well-qualified #1 read as a 33%-capped poor match on any
 * never-asked topic. Now the gauge reads the actual fit and the label rides along
 * as a separate evidence badge (ConfidenceGauge shows both).
 *
 * The raw `score` is a weighted sum (scorer/weights.py): the positive weights
 * (topic_fit .45 + recency .15 + answer_quality .20 + proximity .10) sum to 0.90,
 * so a theoretically-perfect fit scores ~0.90. We normalise against that ceiling
 * to get an absolute 0–100% that means the same thing across queries — a strong
 * candidate reads high even when its confidence label is 低.
 */

/** Sum of the positive scorer weights (scorer/weights.py) — the fit ceiling. */
export const MAX_COMPOSITE_SCORE = 0.9;

/** Fraction of the ring each qualitative level fills; the ConfidenceGauge fallback
 * when no fit percent is supplied. Kept for that fallback only — the gauge magnitude
 * is normally the score-derived fit, not this. */
export const LEVEL_FRACTION: Record<string, number> = { 高: 1, 中: 0.66, 低: 0.33 };

export function levelFraction(level: string): number {
  return LEVEL_FRACTION[level] ?? 0.5;
}

export interface FitInput {
  score: number;
  confidence: string;
}

/** Absolute fit percent (0–100) for one composite score, normalised to the fit
 * ceiling and clamped. Independent of the confidence label (#240). */
export function fitPercent(score: number): number {
  return Math.round(Math.max(0, Math.min(score / MAX_COMPOSITE_SCORE, 1)) * 100);
}

/**
 * Absolute fit percents for a list of candidates.
 *
 * Each card reads its own score-derived fit — decoupled from the confidence label,
 * so a strong #1 on a never-asked topic (label 低) still shows a high gauge, while
 * a genuinely weak top pick reads low instead of being overstated as 100%.
 */
export function fitPercents(candidates: readonly FitInput[]): number[] {
  return candidates.map((c) => fitPercent(c.score));
}
