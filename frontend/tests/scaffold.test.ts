// Scaffold test — keeps CI green before real tests exist.
// Replace once the spec is finalized.
import { describe, expect, it } from "vitest";

describe("scaffold", () => {
  it("runs the test toolchain", () => {
    expect(1 + 1).toBe(2);
  });
});
