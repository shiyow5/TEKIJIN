import {
  advanceSession,
  ApiError,
  getDashboard,
  getDocument,
  getEmployees,
  getHandoff,
  getInbox,
  getRecentQuestions,
  postAnswer,
  postAsk,
} from "@/lib/api-client";
import type { AskRequest, HandoffResponse, ResumeRequest } from "@/lib/api-types";
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

  it("normalizes a trailing slash on the baseUrl override", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ session_id: "abc-123", status: "accepted" }));

    await postAsk(REQUEST, { fetchImpl, baseUrl: "https://api.example.com/" });

    expect(fetchImpl.mock.calls[0][0]).toBe("https://api.example.com/ask");
  });

  it("throws ApiError using the `error` field when `detail` is absent", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ error: "internal error" }, { ok: false, status: 500 }));

    await expect(postAsk(REQUEST, { fetchImpl })).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      message: "internal error",
    });
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

describe("postAnswer", () => {
  const RESUME: ResumeRequest = { session_id: "abc-123", reply: "UTMです" };

  it("POSTs to {base}/answer with JSON headers and the resume body", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ session_id: "abc-123", status: "accepted" }));

    const result = await postAnswer(RESUME, { fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/answer`);
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(JSON.parse(init?.body as string)).toEqual(RESUME);
    expect(result).toEqual({ session_id: "abc-123", status: "accepted" });
  });

  it("throws ApiError on a non-2xx response", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: "bad" }, { ok: false, status: 409 }));

    await expect(postAnswer(RESUME, { fetchImpl })).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
    });
  });
});

describe("getHandoff", () => {
  const HANDOFF: HandoffResponse = {
    session_id: "abc-123",
    question: "UTM移行時の注意点",
    asker: { id: "E010", name: "藤田 悠斗", dept: "第3営業部" },
    topics: ["ネットワーク・VPN"],
    products: ["UTM"],
    situation: "移行",
    missing: [],
    responder: {
      person_id: "E001",
      name: "高梨 健太",
      dept: "技術部",
      score: 0.9,
      confidence: "高",
      reasons: [{ type: "cert", detail: "情報処理安全確保支援士" }],
    },
    draft: "高梨さんへの依頼文",
    reuse_count: 7,
    helpful_answer_count: 5,
  };

  it("GETs {base}/handoff/{id} and returns the payload", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(HANDOFF));

    const result = await getHandoff("abc-123", { fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/handoff/abc-123`);
    expect(init?.method).toBe("GET");
    expect(result).toEqual(HANDOFF);
  });

  it("url-encodes the session id path segment", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(HANDOFF));

    await getHandoff("a b/c", { fetchImpl });

    expect(fetchImpl.mock.calls[0][0]).toBe(`${DEFAULT_API_BASE_URL}/handoff/a%20b%2Fc`);
  });

  it("throws ApiError on a non-2xx response (e.g. 404 no handoff)", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse({ detail: "no responder handoff" }, { ok: false, status: 404 }),
      );

    await expect(getHandoff("gone", { fetchImpl })).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });
});

describe("advanceSession", () => {
  function sseResponse(): Response {
    return {
      ok: true,
      status: 200,
      text: async () => "event: done\ndata: {}\n\n",
    } as Response;
  }

  it("GETs {base}/events/{id} and drains the stream to completion", async () => {
    const textSpy = vi.fn(async () => "event: done\ndata: {}\n\n");
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue({ ok: true, status: 200, text: textSpy } as unknown as Response);

    await advanceSession("abc-123", { fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/events/abc-123`);
    expect(init?.method).toBe("GET");
    expect(textSpy).toHaveBeenCalledTimes(1); // read to completion
  });

  it("url-encodes the session id path segment", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(sseResponse());
    await advanceSession("a b/c", { fetchImpl });
    expect(fetchImpl.mock.calls[0][0]).toBe(`${DEFAULT_API_BASE_URL}/events/a%20b%2Fc`);
  });

  it("swallows a transport error (best-effort: the outcome is already recorded)", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new Error("network"));
    await expect(advanceSession("x", { fetchImpl })).resolves.toBeUndefined();
  });
});

describe("getDashboard", () => {
  it("GETs {base}/dashboard and returns the payload", async () => {
    const body = { total_questions: 150, self_resolution_rate: 0.4, latest_eval: null };
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));

    const result = await getDashboard({ fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/dashboard`);
    expect(init?.method).toBe("GET");
    expect(result).toMatchObject(body);
  });

  it("throws ApiError on a non-2xx response", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: "boom" }, { ok: false, status: 500 }));
    await expect(getDashboard({ fetchImpl })).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
    });
  });
});

describe("getEmployees", () => {
  it("GETs /employees and unwraps the employees array", async () => {
    const employees = [
      { id: "E001", name: "山田 太郎", dept: "営業部" },
      { id: "E002", name: "佐藤 花子", dept: "技術部" },
    ];
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ employees }));

    const result = await getEmployees({ fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/employees`);
    expect(init?.method).toBe("GET");
    expect(result).toEqual(employees);
  });

  it("throws ApiError on a non-2xx response", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: "boom" }, { ok: false, status: 503 }));
    await expect(getEmployees({ fetchImpl })).rejects.toBeInstanceOf(ApiError);
  });
});

describe("getInbox", () => {
  const ITEMS = [
    {
      session_id: "sess-1",
      question_id: "api_q1",
      question: "UTM 移行の注意点",
      topics: ["ネットワーク"],
      asker: { id: "E010", name: "藤田 悠斗", dept: "第3営業部" },
      created_at: "2026-08-23T09:30:00",
    },
  ];

  it("GETs /inbox with the responder_id query and unwraps items", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items: ITEMS }));

    const result = await getInbox("E001", { fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/inbox?responder_id=E001`);
    expect(init?.method).toBe("GET");
    expect(result).toEqual(ITEMS);
  });

  it("url-encodes the responder_id", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items: [] }));
    await getInbox("E 1/2", { fetchImpl });
    expect(fetchImpl.mock.calls[0][0]).toBe(`${DEFAULT_API_BASE_URL}/inbox?responder_id=E%201%2F2`);
  });

  it("throws ApiError on a non-2xx response", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: "bad" }, { ok: false, status: 422 }));
    await expect(getInbox("nope", { fetchImpl })).rejects.toBeInstanceOf(ApiError);
  });
});

describe("getRecentQuestions", () => {
  const ITEMS = [
    {
      question_id: "q1",
      title: "UTM移行",
      resolved: true,
      responder_name: "高梨 健太",
      created_at: "2026-08-20T10:00:00",
    },
  ];

  it("GETs /questions with the asker_id query and unwraps items", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items: ITEMS }));

    const result = await getRecentQuestions("E001", { fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/questions?asker_id=E001`);
    expect(init?.method).toBe("GET");
    expect(result).toEqual(ITEMS);
  });

  it("throws ApiError on a non-2xx response", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: "bad" }, { ok: false, status: 422 }));
    await expect(getRecentQuestions("nope", { fetchImpl })).rejects.toBeInstanceOf(ApiError);
  });
});

describe("getDocument", () => {
  const DOC = {
    id: "doc_001",
    title: "社内IT手順書",
    body: "PCセットアップ手順…",
    source: "社内Wiki",
    updated_at: "2026-08-01T09:00:00",
  };

  it("GETs /documents/{id} (id encoded) and returns the document", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(DOC));

    const result = await getDocument("doc_001", { fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`${DEFAULT_API_BASE_URL}/documents/doc_001`);
    expect(init?.method).toBe("GET");
    expect(result).toEqual(DOC);
  });

  it("throws ApiError with the 404 status for an unknown document", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse({ detail: "document not found" }, { ok: false, status: 404 }),
      );
    await expect(getDocument("nope", { fetchImpl })).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
    });
  });
});
