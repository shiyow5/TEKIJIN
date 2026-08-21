import { DEFAULT_API_BASE_URL, getApiBaseUrl } from "@/lib/config";
import { afterEach, describe, expect, it } from "vitest";

const ENV_KEY = "NEXT_PUBLIC_API_BASE_URL";

describe("getApiBaseUrl", () => {
  const original = process.env[ENV_KEY];

  afterEach(() => {
    if (original === undefined) {
      delete process.env[ENV_KEY];
    } else {
      process.env[ENV_KEY] = original;
    }
  });

  it("falls back to the default when the env var is unset", () => {
    delete process.env[ENV_KEY];
    expect(getApiBaseUrl()).toBe(DEFAULT_API_BASE_URL);
  });

  it("falls back to the default when the env var is blank", () => {
    process.env[ENV_KEY] = "   ";
    expect(getApiBaseUrl()).toBe(DEFAULT_API_BASE_URL);
  });

  it("uses the env var when set", () => {
    process.env[ENV_KEY] = "https://api.example.com";
    expect(getApiBaseUrl()).toBe("https://api.example.com");
  });

  it("trims a trailing slash so callers can concatenate a path", () => {
    process.env[ENV_KEY] = "https://api.example.com/";
    expect(getApiBaseUrl()).toBe("https://api.example.com");
  });
});
