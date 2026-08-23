import { describe, expect, it } from "vitest";
import { ROUTE_LABELS, routeLabel } from "@/lib/routes";

describe("routeLabel", () => {
  it("maps every known route enum to a Japanese label", () => {
    expect(routeLabel("person")).toBe("人に聞く");
    expect(routeLabel("prior_answer")).toBe("過去の回答");
    expect(routeLabel("document")).toBe("社内文書");
    expect(routeLabel("none")).toBe("該当なし");
  });

  it("falls back to the raw string for an unknown route", () => {
    expect(routeLabel("brand_new_route")).toBe("brand_new_route");
  });

  it("keeps the label table in sync with the known enum set", () => {
    expect(Object.keys(ROUTE_LABELS).sort()).toEqual(
      ["document", "none", "person", "prior_answer"].sort(),
    );
  });
});
