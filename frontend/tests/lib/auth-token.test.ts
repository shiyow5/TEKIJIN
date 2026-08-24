import { getAuthToken, loadStoredToken, setAuthToken } from "@/lib/auth-token";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const KEY = "tekijin.authToken";

beforeEach(() => {
  window.localStorage.clear();
  setAuthToken(null); // reset the in-memory cache between tests
});

afterEach(() => {
  window.localStorage.clear();
});

describe("auth-token", () => {
  it("starts empty", () => {
    expect(getAuthToken()).toBeNull();
  });

  it("set/get round-trips and persists to localStorage", () => {
    setAuthToken("abc.def.ghi");
    expect(getAuthToken()).toBe("abc.def.ghi");
    expect(window.localStorage.getItem(KEY)).toBe("abc.def.ghi");
  });

  it("clears with null and removes the stored value", () => {
    setAuthToken("tok");
    setAuthToken(null);
    expect(getAuthToken()).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("loadStoredToken restores the cache from localStorage", () => {
    window.localStorage.setItem(KEY, "restored-token");
    expect(loadStoredToken()).toBe("restored-token");
    expect(getAuthToken()).toBe("restored-token");
  });

  it("loadStoredToken returns null when nothing is stored", () => {
    expect(loadStoredToken()).toBeNull();
    expect(getAuthToken()).toBeNull();
  });
});
