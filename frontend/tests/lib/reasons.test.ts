import { REASON_LABELS, reasonLabel } from "@/lib/reasons";
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
