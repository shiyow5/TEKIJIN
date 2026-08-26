import { useChatThread } from "@/hooks/useChatThread";
import type { ChatThreadDetail } from "@/lib/api-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getChatThreadMock = vi.fn();
const postMessageMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getChatThread: (...args: unknown[]) => getChatThreadMock(...args),
  postMessage: (...args: unknown[]) => postMessageMock(...args),
}));

const DETAIL: ChatThreadDetail = {
  thread_id: 42,
  question_id: "q_0001",
  question_title: "VPN移行の相談",
  counterpart: { id: "E001", name: "高梨 健太", dept: "技術部" },
  messages: [
    {
      id: 1,
      thread_id: 42,
      sender_id: "E010",
      body: "よろしくお願いします",
      created_at: "2026-08-24T09:00:00",
    },
  ],
  slack_channel_url: null,
};

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
}

describe("useChatThread", () => {
  beforeEach(() => {
    getChatThreadMock.mockReset();
    postMessageMock.mockReset();
    setVisibility("visible");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("stays idle when threadId is null (nothing selected)", () => {
    const { result } = renderHook(() => useChatThread(null, "E010"));
    expect(result.current.phase).toBe("idle");
    expect(getChatThreadMock).not.toHaveBeenCalled();
  });

  it("loads the thread on mount and reaches the ready phase", async () => {
    getChatThreadMock.mockResolvedValue(DETAIL);
    const { result } = renderHook(() => useChatThread(42, "E010"));

    expect(result.current.phase).toBe("loading");
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything());
    expect(result.current.detail).toEqual(DETAIL);
  });

  it("polls again after intervalMs while the tab is visible", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getChatThreadMock.mockResolvedValue(DETAIL);
    const { result } = renderHook(() => useChatThread(42, "E010", { intervalMs: 3_000 }));
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    expect(getChatThreadMock).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(3_000));
    expect(getChatThreadMock).toHaveBeenCalledTimes(2);
  });

  it("does not poll while the tab is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getChatThreadMock.mockResolvedValue(DETAIL);
    const { result } = renderHook(() => useChatThread(42, "E010", { intervalMs: 3_000 }));
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    expect(getChatThreadMock).toHaveBeenCalledTimes(1);

    setVisibility("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(getChatThreadMock).toHaveBeenCalledTimes(1);
  });

  it("re-selecting a different threadId re-fetches", async () => {
    getChatThreadMock.mockResolvedValue(DETAIL);
    const { result, rerender } = renderHook(({ id }) => useChatThread(id, "E010"), {
      initialProps: { id: 42 },
    });
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    rerender({ id: 43 });
    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(43, "E010", expect.anything()),
    );
  });

  it("sends a message, refetches, and clears sending state", async () => {
    getChatThreadMock.mockResolvedValue(DETAIL);
    postMessageMock.mockResolvedValue({
      id: 2,
      thread_id: 42,
      sender_id: "E010",
      body: "承知しました",
      created_at: "2026-08-24T09:05:00",
    });
    const { result } = renderHook(() => useChatThread(42, "E010"));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    act(() => result.current.send("承知しました"));
    expect(result.current.sending).toBe(true);
    expect(postMessageMock).toHaveBeenCalledWith({
      thread_id: 42,
      sender_id: "E010",
      body: "承知しました",
    });

    await waitFor(() => expect(result.current.sending).toBe(false));
    await waitFor(() => expect(getChatThreadMock).toHaveBeenCalledTimes(2));
  });

  it("ignores a send with a blank body", async () => {
    getChatThreadMock.mockResolvedValue(DETAIL);
    const { result } = renderHook(() => useChatThread(42, "E010"));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    act(() => result.current.send("   "));
    expect(postMessageMock).not.toHaveBeenCalled();
  });

  it("sets sendError when postMessage fails, and clears it on the next send attempt", async () => {
    getChatThreadMock.mockResolvedValue(DETAIL);
    postMessageMock.mockRejectedValueOnce(new Error("network"));
    const { result } = renderHook(() => useChatThread(42, "E010"));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    act(() => result.current.send("hi"));
    await waitFor(() => expect(result.current.sendError).toBeTruthy());

    postMessageMock.mockResolvedValueOnce({
      id: 3,
      thread_id: 42,
      sender_id: "E010",
      body: "hi",
      created_at: "2026-08-24T09:06:00",
    });
    act(() => result.current.send("hi"));
    await waitFor(() => expect(result.current.sendError).toBeUndefined());
  });

  it("reaches the error phase when the initial load fails", async () => {
    getChatThreadMock.mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => useChatThread(42, "E010"));
    await waitFor(() => expect(result.current.phase).toBe("error"));
  });

  it("stops polling on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getChatThreadMock.mockResolvedValue(DETAIL);
    const { result, unmount } = renderHook(() => useChatThread(42, "E010", { intervalMs: 3_000 }));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    unmount();
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(getChatThreadMock).toHaveBeenCalledTimes(1);
  });
});
