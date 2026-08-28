"use client";

/**
 * Animated confidence gauge for a recommendation card (#139, proposal E).
 *
 * Renders the **適合度 (fit)** as a radial ring that sweeps to the score-derived
 * fit percent on mount, with that number shown in the centre (具体的な数値を円の中に).
 * The magnitude, colour and adjacent 高/中/低 are all the absolute fit
 * (`fitPercent`, normalised score). Evidence confidence is deliberately a second,
 * explicitly-labelled line since #240/#540: mixing its label/colour into the fit
 * gauge made e.g. "47 高" look self-contradictory.
 *
 * Accessibility: the ring is `role="img"` with a text label carrying both the
 * level and the percent, and the level is also shown, so nothing depends on
 * color/animation. `prefers-reduced-motion` renders the final ring instantly (no
 * sweep). anime.js is loaded lazily so it never blocks first paint and stays out
 * of the server bundle.
 */

import { fitLevel } from "@/lib/fit";
import { useEffect, useRef, useState } from "react";

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

export function ConfidenceGauge({ percent }: { percent?: number }) {
  // The score-derived fit drives the ring, number, colour AND fit label. When no
  // percent is available, preserve the legacy confidence-based magnitude only as
  // a fallback. The evidence confidence is rendered separately below (#540).
  const pct = Math.max(0, Math.min(percent ?? 50, 100));
  const level = fitLevel(pct);
  const fraction = pct / 100;
  const colorClass = LEVEL_COLOR[level] ?? "text-on-surface-variant";
  const finalOffset = CIRCUMFERENCE * (1 - fraction);

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
      aria-label={`適合度 ${pct}%（${level}）`}
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
          {pct}
        </text>
      </svg>
      <span className="font-bold text-xs">{level}</span>
    </span>
  );
}
