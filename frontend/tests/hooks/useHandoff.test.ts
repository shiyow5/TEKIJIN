import { useHandoff } from "@/hooks/useHandoff";
import type { HandoffResponse } from "@/lib/api-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getHandoffMock = vi.fn();
const postAnswerMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getHandoff: (...args: unknown[]) => getHandoffMock(...args),
  postAnswer: (...args: unknown[]) => postAnswerMock(...args),
  ApiError: class ApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

const HANDOFF: HandoffResponse = {
  session_id: "s1",
  question: "UTM移行の注意点",
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

describe("useHandoff", () => {
  beforeEach(() => {
    getHandoffMock.mockReset();
    postAnswerMock.mockReset();
    postAnswerMock.mockResolvedValue({ session_id: "s1", status: "resumed" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the handoff on mount and reaches the ready phase", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    const { result } = renderHook(() => useHandoff("s1"));

    expect(result.current.phase).toBe("loading");
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    expect(getHandoffMock).toHaveBeenCalledWith("s1");
    expect(result.current.handoff).toEqual(HANDOFF);
  });

  it("submits an accepted outcome for the 'answer' action and reaches done", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    const { result } = renderHook(() => useHandoff("s1"));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    act(() => result.current.submit("answer"));

    await waitFor(() => expect(result.current.phase).toBe("done"));
    expect(postAnswerMock).toHaveBeenCalledWith({ session_id: "s1", outcome: "accepted" });
    expect(result.current.action).toBe("answer");
  });

  it("submits a declined outcome for the 'defer' action (今は難しい)", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    const { result } = renderHook(() => useHandoff("s1"));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    act(() => result.current.submit("defer"));

    await waitFor(() => expect(result.current.phase).toBe("done"));
    expect(postAnswerMock).toHaveBeenCalledWith({ session_id: "s1", outcome: "declined" });
    expect(result.current.action).toBe("defer");
  });

  it("submits a declined outcome for the 'refer' action (別の人を薦める, interim)", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    const { result } = renderHook(() => useHandoff("s1"));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    act(() => result.current.submit("refer"));

    await waitFor(() => expect(result.current.phase).toBe("done"));
    expect(postAnswerMock).toHaveBeenCalledWith({ session_id: "s1", outcome: "declined" });
    expect(result.current.action).toBe("refer");
  });

  it("treats a 404/409 load error as 'gone' (no handoff pending)", async () => {
    const { ApiError } = await import("@/lib/api-client");
    getHandoffMock.mockRejectedValue(new ApiError(409, "not awaiting a responder outcome"));
    const { result } = renderHook(() => useHandoff("s1"));

    await waitFor(() => expect(result.current.phase).toBe("error"));
    expect(result.current.errorKind).toBe("gone");
  });

  it("treats a generic load failure as a retryable load error", async () => {
    getHandoffMock.mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => useHandoff("s1"));

    await waitFor(() => expect(result.current.phase).toBe("error"));
    expect(result.current.errorKind).toBe("load");
  });

  it("returns to ready with a submit error when the outcome POST fails", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    postAnswerMock.mockRejectedValueOnce(new Error("network"));
    const { result } = renderHook(() => useHandoff("s1"));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    act(() => result.current.submit("answer"));

    await waitFor(() => expect(result.current.submitError).toBeTruthy());
    expect(result.current.phase).toBe("ready"); // retryable, not dead-ended
  });
});
