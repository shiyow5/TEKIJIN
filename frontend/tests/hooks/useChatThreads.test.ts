import { useChatThreads } from "@/hooks/useChatThreads";
import type { ChatThreadSummary } from "@/lib/api-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getChatThreadsMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getChatThreads: (...args: unknown[]) => getChatThreadsMock(...args),
}));

const THREAD: ChatThreadSummary = {
  thread_id: 42,
  question_id: "q_0001",
  question_title: "VPN移行の相談",
  counterpart: { id: "E001", name: "高梨 健太", dept: "技術部" },
  last_message: "承知しました",
  last_message_at: "2026-08-24T10:00:00",
  created_at: "2026-08-24T09:00:00",
};

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
}

describe("useChatThreads", () => {
  beforeEach(() => {
    getChatThreadsMock.mockReset();
    setVisibility("visible");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("loads the thread list on mount and reaches the ready phase", async () => {
    getChatThreadsMock.mockResolvedValue([THREAD]);
    const { result } = renderHook(() => useChatThreads("E010"));

    expect(result.current.phase).toBe("loading");
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    expect(getChatThreadsMock).toHaveBeenCalledWith("E010", expect.anything());
    expect(result.current.threads).toEqual([THREAD]);
  });

  it("stays idle without fetching while employeeId is null", () => {
    const { result } = renderHook(() => useChatThreads(null));
    expect(result.current.phase).toBe("loading");
    expect(result.current.threads).toEqual([]);
    expect(getChatThreadsMock).not.toHaveBeenCalled();
  });

  it("polls again after intervalMs while the tab is visible", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getChatThreadsMock.mockResolvedValue([THREAD]);
    const { result } = renderHook(() => useChatThreads("E010", { intervalMs: 5_000 }));

    await waitFor(() => expect(result.current.phase).toBe("ready"));
    expect(getChatThreadsMock).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(5_000));
    expect(getChatThreadsMock).toHaveBeenCalledTimes(2);
  });

  it("does not poll while the tab is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getChatThreadsMock.mockResolvedValue([THREAD]);
    const { result } = renderHook(() => useChatThreads("E010", { intervalMs: 5_000 }));
    await waitFor(() => expect(result.current.phase).toBe("ready"));
    expect(getChatThreadsMock).toHaveBeenCalledTimes(1);

    setVisibility("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    expect(getChatThreadsMock).toHaveBeenCalledTimes(1); // still just the initial load

    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(getChatThreadsMock).toHaveBeenCalledTimes(2));
  });

  it("keeps the last-known list on a poll failure after a successful load", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getChatThreadsMock.mockResolvedValueOnce([THREAD]).mockRejectedValueOnce(new Error("network"));
    const { result } = renderHook(() => useChatThreads("E010", { intervalMs: 5_000 }));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    await act(() => vi.advanceTimersByTimeAsync(5_000));
    await waitFor(() => expect(getChatThreadsMock).toHaveBeenCalledTimes(2));
    expect(result.current.phase).toBe("ready");
    expect(result.current.threads).toEqual([THREAD]);
  });

  it("reaches the error phase when the initial load fails", async () => {
    getChatThreadsMock.mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => useChatThreads("E010"));
    await waitFor(() => expect(result.current.phase).toBe("error"));
  });

  it("stops polling on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getChatThreadsMock.mockResolvedValue([THREAD]);
    const { result, unmount } = renderHook(() => useChatThreads("E010", { intervalMs: 5_000 }));
    await waitFor(() => expect(result.current.phase).toBe("ready"));

    unmount();
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    expect(getChatThreadsMock).toHaveBeenCalledTimes(1);
  });
});
