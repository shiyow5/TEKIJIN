import { formatDateJst, formatDateTimeJst } from "@/lib/datetime";
import { describe, expect, it } from "vitest";

describe("formatDateTimeJst", () => {
  it("converts a naive (timezone-less) backend timestamp from UTC to JST", () => {
    // Backend naive strings are actually UTC wall-clock time (#418) — 06:30 UTC
    // is 15:30 JST (+9h), not "06:30" taken at face value.
    expect(formatDateTimeJst("2026-08-26T06:30:00")).toBe("2026-08-26 15:30");
  });

  it("rolls the date forward across the JST midnight boundary", () => {
    // 20:30 UTC + 9h = 05:30 the NEXT day in JST — this is exactly the case
    // that made the old slice-based formatting look "mostly right" (date
    // usually survived) except right around this boundary.
    expect(formatDateTimeJst("2026-08-26T20:30:00")).toBe("2026-08-27 05:30");
  });

  it("respects an explicit Z suffix instead of double-converting", () => {
    expect(formatDateTimeJst("2026-08-26T06:30:00Z")).toBe("2026-08-26 15:30");
  });

  it("respects an explicit numeric offset instead of assuming UTC", () => {
    // Already JST (+09:00) -> no further shift.
    expect(formatDateTimeJst("2026-08-26T15:30:00+09:00")).toBe("2026-08-26 15:30");
  });

  it("returns null for missing or unparseable input", () => {
    expect(formatDateTimeJst(null)).toBeNull();
    expect(formatDateTimeJst(undefined)).toBeNull();
    expect(formatDateTimeJst("")).toBeNull();
    expect(formatDateTimeJst("not-a-date")).toBeNull();
  });
});

describe("formatDateJst", () => {
  it("returns just the JST date portion", () => {
    expect(formatDateJst("2026-08-26T20:30:00")).toBe("2026-08-27");
  });

  it("returns null for missing or unparseable input", () => {
    expect(formatDateJst(null)).toBeNull();
    expect(formatDateJst("garbage")).toBeNull();
  });
});
