import { formatConfidence } from "@/lib/format";
import { describe, expect, it } from "vitest";

describe("formatConfidence", () => {
  it("renders a 0..1 float as a whole percent", () => {
    expect(formatConfidence(0.85)).toBe("85%");
    expect(formatConfidence(0)).toBe("0%");
    expect(formatConfidence(1)).toBe("100%");
  });

  it("rounds to the nearest percent", () => {
    expect(formatConfidence(0.626)).toBe("63%");
  });

  it("clamps out-of-range values", () => {
    expect(formatConfidence(1.5)).toBe("100%");
    expect(formatConfidence(-0.2)).toBe("0%");
  });

  it("returns a dash for non-finite input", () => {
    expect(formatConfidence(Number.NaN)).toBe("—");
  });
});
