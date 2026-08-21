import { ApiError, postAsk } from "@/lib/api-client";
import type { AskRequest } from "@/lib/api-types";
import { DEFAULT_API_BASE_URL } from "@/lib/config";
import { describe, expect, it, vi } from "vitest";

const REQUEST: AskRequest = {
  asker_id: 1,
  question: "UTMの移行時の注意点",
  session_id: "abc-123",
};

function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}): Response {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  } as Response;
}

describe("postAsk", () => {
  it("POSTs to {base}/ask with JSON headers and body", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ session_id: "abc-123", status: "accepted" }));

    const result = await postAsk(REQUEST, { fetchImpl });

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/ask`);
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(JSON.parse(init?.body as string)).toEqual(REQUEST);
    expect(result).toEqual({ session_id: "abc-123", status: "accepted" });
  });

  it("uses an explicit baseUrl override", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ session_id: "abc-123", status: "accepted" }));

    await postAsk(REQUEST, { fetchImpl, baseUrl: "https://api.example.com" });

    expect(fetchImpl.mock.calls[0][0]).toBe("https://api.example.com/ask");
  });

  it("throws ApiError with the status and detail on a non-2xx response", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse({ detail: "question must not be blank" }, { ok: false, status: 422 }),
      );

    await expect(postAsk(REQUEST, { fetchImpl })).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      message: "question must not be blank",
    });
  });

  it("throws ApiError with a generic message when the error body is not JSON", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    } as unknown as Response);

    await expect(postAsk(REQUEST, { fetchImpl })).rejects.toBeInstanceOf(ApiError);
    await expect(postAsk(REQUEST, { fetchImpl })).rejects.toThrow("status 500");
  });
});
