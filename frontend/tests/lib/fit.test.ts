import { MAX_COMPOSITE_SCORE, fitPercent, fitPercents, levelFraction } from "@/lib/fit";
import { describe, expect, it } from "vitest";

describe("levelFraction", () => {
  it("maps the qualitative levels and defaults unknown to half", () => {
    expect(levelFraction("高")).toBe(1);
    expect(levelFraction("中")).toBe(0.66);
    expect(levelFraction("低")).toBe(0.33);
    expect(levelFraction("不明")).toBe(0.5);
  });
});

describe("fitPercent", () => {
  it("normalises a composite score against the fit ceiling (0.9 -> 100%)", () => {
    expect(fitPercent(MAX_COMPOSITE_SCORE)).toBe(100);
    expect(fitPercent(0.45)).toBe(50);
    expect(fitPercent(0)).toBe(0);
  });

  it("clamps: a score above the ceiling caps at 100, a negative score floors at 0", () => {
    expect(fitPercent(1.0)).toBe(100); // 1.0/0.9 -> clamp 100
    expect(fitPercent(-0.5)).toBe(0);
  });
});

describe("fitPercents", () => {
  it("returns each candidate's absolute fit, independent of order", () => {
    expect(
      fitPercents([
        { score: 0.9, confidence: "低" },
        { score: 0.45, confidence: "高" },
        { score: 0.18, confidence: "中" },
      ]),
    ).toEqual([100, 50, 20]);
  });

  it("is decoupled from the confidence label — a 低 top still reads high (#240)", () => {
    // A strong candidate on a never-asked topic: label 低 but a high fit gauge,
    // instead of the old label-anchored 33% cap.
    const strongLow = fitPercents([{ score: 0.74, confidence: "低" }]);
    const strongHigh = fitPercents([{ score: 0.74, confidence: "高" }]);
    expect(strongLow).toEqual(strongHigh); // label does not change the gauge
    expect(strongLow[0]).toBeGreaterThan(33); // no longer capped at 33
  });

  it("differentiates candidates by their own score, not the shared label", () => {
    const out = fitPercents([
      { score: 0.9, confidence: "高" },
      { score: 0.81, confidence: "高" },
      { score: 0.54, confidence: "高" },
    ]);
    expect(out).toEqual([100, 90, 60]);
    expect(new Set(out).size).toBeGreaterThan(1);
  });

  it("returns an empty array for no candidates", () => {
    expect(fitPercents([])).toEqual([]);
  });
});
