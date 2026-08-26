import { SESSION_ID_PATTERN, createSessionId, isValidSessionId } from "@/lib/session";
import { afterEach, describe, expect, it } from "vitest";

describe("isValidSessionId", () => {
  it("accepts path-safe ids (alphanumeric, dash, underscore)", () => {
    expect(isValidSessionId("abc-123_XYZ")).toBe(true);
    expect(isValidSessionId(crypto.randomUUID())).toBe(true);
  });

  it("rejects empty and path-unsafe ids", () => {
    expect(isValidSessionId("")).toBe(false);
    expect(isValidSessionId("has/slash")).toBe(false);
    expect(isValidSessionId("has space")).toBe(false);
    expect(isValidSessionId("dot.dot")).toBe(false);
  });
});

describe("createSessionId", () => {
  const originalCrypto = globalThis.crypto;

  afterEach(() => {
    Object.defineProperty(globalThis, "crypto", {
      value: originalCrypto,
      configurable: true,
    });
  });

  it("returns an id matching the backend pattern", () => {
    const id = createSessionId();
    expect(id).toMatch(SESSION_ID_PATTERN);
  });

  it("falls back to a valid id when crypto.randomUUID is unavailable", () => {
    Object.defineProperty(globalThis, "crypto", {
      value: {},
      configurable: true,
    });
    const id = createSessionId();
    expect(id.startsWith("s-")).toBe(true);
    expect(isValidSessionId(id)).toBe(true);
  });
});
