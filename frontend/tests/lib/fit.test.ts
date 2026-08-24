import { levelFraction, relativeFitPercents } from "@/lib/fit";
import { describe, expect, it } from "vitest";

describe("levelFraction", () => {
  it("maps the qualitative levels and defaults unknown to half", () => {
    expect(levelFraction("高")).toBe(1);
    expect(levelFraction("中")).toBe(0.66);
    expect(levelFraction("低")).toBe(0.33);
    expect(levelFraction("不明")).toBe(0.5);
  });
});

describe("relativeFitPercents", () => {
  it("anchors the top to its level ceiling and scales the rest by score ratio", () => {
    // Top is 高 (ceiling 100). #2 has half the score, #3 a quarter.
    const out = relativeFitPercents([
      { score: 0.8, confidence: "高" },
      { score: 0.4, confidence: "高" },
      { score: 0.2, confidence: "中" },
    ]);
    expect(out).toEqual([100, 50, 25]);
  });

  it("caps the anchor at the top's level, so a weak top is not overstated", () => {
    // Top is 中 (ceiling 66); equally-scored second stays at the same ceiling.
    const out = relativeFitPercents([
      { score: 0.5, confidence: "中" },
      { score: 0.25, confidence: "中" },
    ]);
    expect(out).toEqual([66, 33]);
  });

  it("differentiates candidates that all share the 高 level", () => {
    const out = relativeFitPercents([
      { score: 1.0, confidence: "高" },
      { score: 0.9, confidence: "高" },
      { score: 0.6, confidence: "高" },
    ]);
    // No longer all 100 — the #222 symptom is gone.
    expect(out[0]).toBe(100);
    expect(out[1]).toBe(90);
    expect(out[2]).toBe(60);
    expect(new Set(out).size).toBeGreaterThan(1);
  });

  it("falls back to each level's own percentage when the top score is not positive", () => {
    const out = relativeFitPercents([
      { score: 0, confidence: "高" },
      { score: -1, confidence: "低" },
    ]);
    expect(out).toEqual([100, 33]);
  });

  it("clamps negative scores to 0 and never exceeds the anchor", () => {
    const out = relativeFitPercents([
      { score: 1.0, confidence: "高" },
      { score: -0.5, confidence: "低" },
    ]);
    expect(out).toEqual([100, 0]);
  });

  it("returns an empty array for no candidates", () => {
    expect(relativeFitPercents([])).toEqual([]);
  });
});
