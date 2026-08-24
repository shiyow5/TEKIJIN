"use client";

/**
 * Animated confidence gauge for a recommendation card (#139, proposal E).
 *
 * Renders the qualitative fit signal (適合度 高/中/低) as a radial ring that
 * sweeps to its level on mount, with a PERCENTAGE shown in the centre of the
 * ring (requested: 具体的な数値を円の中に). The percentage prefers the continuous
 * `fitScore` (0..1, the scorer's topic_fit) when given, so two candidates
 * sharing the same 高/中/低 label are still visibly distinguishable (#205) —
 * falling back to the qualitative level's own magnitude (高=100 / 中=66 /
 * 低=33) when `fitScore` is omitted. Either way this is NOT the raw internal
 * composite `score` (a weighted ranking value, not a percentage, and pulled
 * negative by the load penalty), which stays unsurfaced per CandidateCard's
 * contract.
 *
 * Accessibility: the ring is `role="img"` with a text label carrying both the
 * level and the percent, and the level is also shown, so nothing depends on
 * color/animation. `prefers-reduced-motion` renders the final ring instantly (no
 * sweep). anime.js is loaded lazily so it never blocks first paint and stays out
 * of the server bundle.
 */

import { useEffect, useRef, useState } from "react";

/** Fraction of the ring each qualitative level fills. Unknown → half (neutral). */
const LEVEL_FRACTION: Record<string, number> = { 高: 1, 中: 0.66, 低: 0.33 };
/** Tailwind text-color token per level; the ring strokes `currentColor`. */
const LEVEL_COLOR: Record<string, string> = {
  高: "text-primary",
  中: "text-secondary",
  低: "text-on-surface-variant",
};

const R = 16;
const CIRCUMFERENCE = 2 * Math.PI * R;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function ConfidenceGauge({ level, fitScore }: { level: string; fitScore?: number }) {
  const fraction =
    typeof fitScore === "number" && Number.isFinite(fitScore)
      ? Math.min(Math.max(fitScore, 0), 1)
      : (LEVEL_FRACTION[level] ?? 0.5);
  const colorClass = LEVEL_COLOR[level] ?? "text-on-surface-variant";
  const finalOffset = CIRCUMFERENCE * (1 - fraction);
  // Concrete number shown inside the ring — the magnitude the ring draws.
  const percent = Math.round(fraction * 100);

  const arcRef = useRef<SVGCircleElement | null>(null);
  // Start empty so the sweep is visible; reduced-motion jumps straight to final.
  const [reduced] = useState(prefersReducedMotion);

  useEffect(() => {
    const el = arcRef.current;
    if (el === null) return;
    if (reduced) {
      el.style.strokeDashoffset = String(finalOffset);
      return;
    }
    let cancelled = false;
    // Lazy import keeps anime.js out of the initial/server bundle (#139 NFR).
    import("animejs")
      .then(({ animate }) => {
        if (cancelled || arcRef.current === null) return;
        animate(arcRef.current, {
          strokeDashoffset: [CIRCUMFERENCE, finalOffset],
          duration: 750,
          ease: "outCubic",
        });
      })
      .catch(() => {
        // Animation is a progressive enhancement; on failure show the final ring.
        if (!cancelled && arcRef.current) {
          arcRef.current.style.strokeDashoffset = String(finalOffset);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reduced, finalOffset]);

  return (
    <span
      role="img"
      aria-label={`適合度 ${level}（${percent}%）`}
      className={`inline-flex items-center gap-xs ${colorClass}`}
    >
      <svg width="44" height="44" viewBox="0 0 40 40" aria-hidden="true" className="shrink-0">
        <circle
          cx="20"
          cy="20"
          r={R}
          fill="none"
          strokeWidth="4"
          className="text-outline-variant"
          stroke="currentColor"
          opacity={0.35}
        />
        <circle
          ref={arcRef}
          cx="20"
          cy="20"
          r={R}
          fill="none"
          stroke="currentColor"
          strokeWidth="4"
          strokeLinecap="round"
          transform="rotate(-90 20 20)"
          style={{
            strokeDasharray: CIRCUMFERENCE,
            strokeDashoffset: reduced ? finalOffset : CIRCUMFERENCE,
          }}
        />
        {/* Concrete fit number inside the ring (円の中に数値). */}
        <text
          x="20"
          y="20"
          textAnchor="middle"
          dominantBaseline="central"
          className="fill-current font-bold"
          style={{ fontSize: "11px" }}
        >
          {percent}
        </text>
      </svg>
      <span className="font-bold text-xs">{level}</span>
    </span>
  );
}
