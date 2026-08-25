import { describe, expect, it } from "vitest";

/**
 * Guard for the test environment itself, not for any source module.
 *
 * `window.localStorage` throws `SecurityError` when the document origin is
 * opaque. `CurrentUserProvider.test.tsx`, `auth-token.test.ts` and
 * `api-client-auth.test.ts` all call `window.localStorage.clear()` in a
 * `beforeEach`, so that one condition fails every test in those files — 18 of
 * them — and each failure points at the `clear()` line instead of at the
 * environment. This test states the requirement in one place, so a broken
 * environment names its own cause.
 *
 * The production code does NOT need storage to work (`auth-token.ts` and
 * `CurrentUserProvider.tsx` both fall back when it throws — private mode,
 * disabled storage). Only the tests assume it.
 */
describe("test environment", () => {
  it("has a usable localStorage (an opaque origin would break 18 unrelated tests)", () => {
    expect(() => window.localStorage.clear()).not.toThrow();
    window.localStorage.setItem("tekijin.probe", "ok");
    expect(window.localStorage.getItem("tekijin.probe")).toBe("ok");
    window.localStorage.removeItem("tekijin.probe");
  });
});
