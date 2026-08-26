import { useRecentQuestions } from "@/hooks/useRecentQuestions";
import type { RecentQuestionItem } from "@/lib/api-types";
import { act, fireEvent, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const getRecentQuestionsMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getRecentQuestions: (...args: unknown[]) => getRecentQuestionsMock(...args),
}));

function item(id: string): RecentQuestionItem {
  return {
    question_id: id,
    title: `質問 ${id}`,
    resolved: false,
    resolution: "pending",
    responder_name: null,
    session_id: `sess-${id}`,
    created_at: "2026-08-27T00:00:00+09:00",
  };
}

afterEach(() => {
  getRecentQuestionsMock.mockReset();
});

describe("useRecentQuestions", () => {
  it("stays loading and never fetches while there is no user", () => {
    const { result } = renderHook(() => useRecentQuestions(null));
    expect(result.current[0].phase).toBe("loading");
    expect(getRecentQuestionsMock).not.toHaveBeenCalled();
  });

  it("passes the limit through only when provided", async () => {
    getRecentQuestionsMock.mockResolvedValue([]);
    const { rerender } = renderHook(({ id, limit }) => useRecentQuestions(id, limit), {
      initialProps: {
        id: "E001" as string | null,
        limit: undefined as { limit?: number } | undefined,
      },
    });
    await waitFor(() => expect(getRecentQuestionsMock).toHaveBeenCalled());
    expect(getRecentQuestionsMock).toHaveBeenLastCalledWith("E001"); // no options arg

    getRecentQuestionsMock.mockClear();
    rerender({ id: "E002", limit: { limit: 200 } });
    await waitFor(() => expect(getRecentQuestionsMock).toHaveBeenCalled());
    expect(getRecentQuestionsMock).toHaveBeenLastCalledWith("E002", { limit: 200 });
  });

  it("refetches on window focus (revalidate-on-focus, #468)", async () => {
    getRecentQuestionsMock.mockResolvedValueOnce([item("a")]);
    const { result } = renderHook(() => useRecentQuestions("E001"));
    await waitFor(() => expect(result.current[0].items).toEqual([item("a")]));

    getRecentQuestionsMock.mockResolvedValueOnce([item("a"), item("b")]);
    fireEvent.focus(window);
    await waitFor(() => expect(result.current[0].items).toEqual([item("a"), item("b")]));
  });

  it("ignores an out-of-order response so a slow older refetch never overwrites a newer one", async () => {
    // First (focus) refetch resolves LATE with the old list; second resolves first
    // with the new list. The monotonic request id must keep the newer result.
    let resolveOld: (v: RecentQuestionItem[]) => void = () => {};
    const oldPromise = new Promise<RecentQuestionItem[]>((r) => {
      resolveOld = r;
    });
    // initial mount load
    getRecentQuestionsMock.mockResolvedValueOnce([item("a")]);
    const { result } = renderHook(() => useRecentQuestions("E001"));
    await waitFor(() => expect(result.current[0].items).toEqual([item("a")]));

    getRecentQuestionsMock.mockReturnValueOnce(oldPromise); // focus #1 (slow)
    getRecentQuestionsMock.mockResolvedValueOnce([item("a"), item("b")]); // focus #2 (fast)
    fireEvent.focus(window);
    fireEvent.focus(window);
    await waitFor(() => expect(result.current[0].items).toEqual([item("a"), item("b")]));

    // The slow older request lands last — it must be discarded, not applied.
    await act(async () => {
      resolveOld([item("a")]);
      await Promise.resolve();
    });
    expect(result.current[0].items).toEqual([item("a"), item("b")]);
  });
});
