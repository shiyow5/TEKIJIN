import type { Reason } from "@/lib/api-types";
import { REASON_LABELS, answersEvidence, reasonLabel } from "@/lib/reasons";
import { describe, expect, it } from "vitest";

describe("reasonLabel", () => {
  it("maps known reason types to Japanese labels", () => {
    expect(reasonLabel("cert")).toBe(REASON_LABELS.cert);
    expect(reasonLabel("answers")).toBe("過去回答");
    expect(reasonLabel("load")).toBe("現在の負荷");
    // `skill` (inferred) is distinct from `self` (self-declared).
    expect(reasonLabel("skill")).toBe("推定スキル");
    expect(reasonLabel("self")).toBe("自己申告");
  });

  it("falls back to the raw type for unknown reasons", () => {
    expect(reasonLabel("mystery")).toBe("mystery");
  });
});

describe("answersEvidence", () => {
  it("returns the answers reason detail verbatim (real backend format)", () => {
    const reasons: Reason[] = [
      { type: "cert", detail: "第一種電気工事士" },
      { type: "answers", detail: "類似の質問に過去5件回答（うち有用と評価3件）" },
    ];
    expect(answersEvidence(reasons)).toBe("類似の質問に過去5件回答（うち有用と評価3件）");
  });

  it("returns null when there is no answers reason", () => {
    expect(answersEvidence([{ type: "cert", detail: "資格" }])).toBeNull();
  });

  it("never synthesises a reuse count from other reason types", () => {
    // The backend does not expose a raw reuse count; only the `answers` detail
    // is used, so a non-answers reason yields null (no fabricated number).
    expect(answersEvidence([{ type: "project", detail: "同種の案件を8件担当" }])).toBeNull();
  });
});
