import { getMe, postAsk, postLogin, postLogout } from "@/lib/api-client";
import { setAuthToken } from "@/lib/auth-token";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  window.localStorage.clear();
  setAuthToken(null);
});

afterEach(() => {
  setAuthToken(null);
});

describe("api-client auth wiring", () => {
  it("omits the Authorization header when logged out", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    await getMe({ fetchImpl, baseUrl: "http://api" });
    const headers = (fetchImpl.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it("adds the Authorization header from the current token (GET and POST)", async () => {
    setAuthToken("tok-123");
    // Fresh Response per call — a body can only be read once.
    const fetchImpl = vi.fn((_url?: unknown, _init?: unknown) =>
      Promise.resolve(jsonResponse({ ok: true })),
    );

    await getMe({ fetchImpl, baseUrl: "http://api" });
    let headers = (fetchImpl.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-123");

    await postAsk(
      { asker_id: 1, question: "q", session_id: "s1" },
      { fetchImpl, baseUrl: "http://api" },
    );
    headers = (fetchImpl.mock.calls[1][1] as RequestInit).headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-123");
    expect(headers["Content-Type"]).toBe("application/json");
  });

  it("postLogin posts credentials and returns the token + principal", async () => {
    const principal = { id: "E001", name: "u", dept: null, is_admin: false };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ access_token: "t", token_type: "bearer", principal }));
    const result = await postLogin(
      { email: "a@x", password: "pw" },
      { fetchImpl, baseUrl: "http://api" },
    );
    expect(result.access_token).toBe("t");
    expect(result.principal).toEqual(principal);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("http://api/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ email: "a@x", password: "pw" });
  });

  it("postLogout swallows a transport error (best-effort)", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("network"));
    await expect(postLogout({ fetchImpl, baseUrl: "http://api" })).resolves.toBeUndefined();
  });
});
