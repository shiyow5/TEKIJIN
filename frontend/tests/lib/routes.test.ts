import { ROUTE_LABELS, routeLabel } from "@/lib/routes";
import { describe, expect, it } from "vitest";

describe("routeLabel", () => {
  it("maps every backend route enum to a Japanese label", () => {
    // Mirrors backend Route = Literal["person", "prior_answer", "document"].
    expect(routeLabel("person")).toBe("人に聞く");
    expect(routeLabel("prior_answer")).toBe("過去の回答");
    expect(routeLabel("document")).toBe("社内文書");
  });

  it("falls back to the raw string for an unexpected route", () => {
    expect(routeLabel("brand_new_route")).toBe("brand_new_route");
  });

  it("covers exactly the three backend route values", () => {
    expect(Object.keys(ROUTE_LABELS).sort()).toEqual(["document", "person", "prior_answer"].sort());
  });
});
