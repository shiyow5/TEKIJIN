import type { Reason } from "@/lib/api-types";
import { REASON_LABELS, parseReuseCount, reasonLabel } from "@/lib/reasons";
import { describe, expect, it } from "vitest";

describe("reasonLabel", () => {
  it("maps known reason types to Japanese labels", () => {
    expect(reasonLabel("cert")).toBe(REASON_LABELS.cert);
    expect(reasonLabel("answers")).toBe("過去回答");
    expect(reasonLabel("load")).toBe("現在の負荷");
  });

  it("falls back to the raw type for unknown reasons", () => {
    expect(reasonLabel("mystery")).toBe("mystery");
  });
});

describe("parseReuseCount", () => {
  it("extracts the number from an answers reason detail", () => {
    const reasons: Reason[] = [
      { type: "cert", detail: "関連資格保持" },
      { type: "answers", detail: "過去回答: 45件" },
    ];
    expect(parseReuseCount(reasons)).toBe(45);
  });

  it("returns null when there is no answers reason", () => {
    expect(parseReuseCount([{ type: "cert", detail: "資格" }])).toBeNull();
  });

  it("returns null when the answers reason has no number", () => {
    expect(parseReuseCount([{ type: "answers", detail: "たくさん回答" }])).toBeNull();
  });
});
