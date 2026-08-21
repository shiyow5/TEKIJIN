import { DEFAULT_API_BASE_URL, getApiBaseUrl, normalizeBaseUrl } from "@/lib/config";
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

describe("normalizeBaseUrl", () => {
  it("strips one or more trailing slashes", () => {
    expect(normalizeBaseUrl("https://x/")).toBe("https://x");
    expect(normalizeBaseUrl("https://x///")).toBe("https://x");
  });

  it("leaves a slash-free base unchanged", () => {
    expect(normalizeBaseUrl("https://x")).toBe("https://x");
  });
});
