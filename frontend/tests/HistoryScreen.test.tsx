import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { HistoryScreen } from "@/components/HistoryScreen";
import type { RecentQuestionItem } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const getRecentQuestionsMock = vi.fn();
const deleteQuestionMock = vi.fn();
const resolveQuestionMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getRecentQuestions: (...args: unknown[]) => getRecentQuestionsMock(...args),
  deleteQuestion: (...args: unknown[]) => deleteQuestionMock(...args),
  resolveQuestion: (...args: unknown[]) => resolveQuestionMock(...args),
}));

function asUser(id: string | null): CurrentUserContextValue {
  return {
    employees: [],
    currentUserId: id,
    currentUser: null,
    setCurrentUserId: vi.fn(),
    loading: false,
    error: false,
    reload: vi.fn(),
    canSwitch: false,
  };
}

const ITEMS: RecentQuestionItem[] = [
  {
    question_id: "q1",
    title: "UTMの移行時の注意点",
    resolved: true,
    resolution: "person",
    responder_name: "高梨 健太",
    session_id: "sess-q1",
    created_at: "2026-08-20T10:00:00",
  },
  {
    question_id: "q2",
    title: "社内Wi-Fiの申請方法",
    resolved: false,
    resolution: "pending",
    responder_name: null,
    session_id: null,
    created_at: "2026-08-19T09:30:00",
  },
];

beforeEach(() => {
  useCurrentUserMock.mockReset();
  getRecentQuestionsMock.mockReset();
  deleteQuestionMock.mockReset();
  resolveQuestionMock.mockReset();
  resolveQuestionMock.mockResolvedValue({ question_id: "q2", resolved: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("HistoryScreen", () => {
  it("shows loading until the current user resolves", () => {
    useCurrentUserMock.mockReturnValue(asUser(null));
    render(<HistoryScreen />);
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(getRecentQuestionsMock).not.toHaveBeenCalled();
  });

  it("fetches the full history (limit 200) and renders each row", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() =>
      expect(getRecentQuestionsMock).toHaveBeenCalledWith("E001", { limit: 200 }),
    );
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
    // date + resolution note + a result link for the item with a session.
    expect(screen.getByText("2026-08-20 10:00")).toBeInTheDocument();
    expect(screen.getByText("回答者: 高梨 健太")).toBeInTheDocument();
    expect(screen.getByText("取り次ぎ先を調整中")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "結果を見る" })).toHaveAttribute(
      "href",
      "/session/sess-q1",
    );
  });

  it("deletes a question after confirmation and removes it from the list", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    deleteQuestionMock.mockResolvedValue({ question_id: "q2", deleted: true });
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "「社内Wi-Fiの申請方法」を削除" }));
    fireEvent.click(screen.getByRole("button", { name: "削除" }));
    await waitFor(() => expect(deleteQuestionMock).toHaveBeenCalledWith("q2"));
    await waitFor(() => expect(screen.queryByText("社内Wi-Fiの申請方法")).not.toBeInTheDocument());
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
  });

  it("marks a pending question self-resolved after confirmation (#159)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    // The pending item offers a self-resolve control; the resolved one does not.
    expect(screen.getByText("取り次ぎ先を調整中")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "「社内Wi-Fiの申請方法」を自分で解決済みにする" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "解決済みにする" }));

    await waitFor(() => expect(resolveQuestionMock).toHaveBeenCalledWith("q2"));
    // The row updates in place to the self-resolved state (no re-fetch).
    await waitFor(() => expect(screen.getByText("自分で解決")).toBeInTheDocument());
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
  });

  it("keeps a pending question and shows a retry when self-resolve fails (#159)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    resolveQuestionMock.mockRejectedValueOnce(new Error("boom"));
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    fireEvent.click(
      screen.getByRole("button", { name: "「社内Wi-Fiの申請方法」を自分で解決済みにする" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "解決済みにする" }));

    await waitFor(() => expect(resolveQuestionMock).toHaveBeenCalledWith("q2"));
    // The row stays pending and offers a retry (visible label), rather than being lost.
    await waitFor(() => expect(screen.getByText("再試行")).toBeInTheDocument());
    expect(screen.getByText("取り次ぎ先を調整中")).toBeInTheDocument();
    expect(screen.queryByText("自分で解決")).not.toBeInTheDocument();
  });

  it("shows an empty state when there is no history", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E002"));
    getRecentQuestionsMock.mockResolvedValue([]);
    render(<HistoryScreen />);
    await waitFor(() => expect(screen.getByText("まだ質問はありません。")).toBeInTheDocument());
  });

  it("shows an error note when the fetch fails", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockRejectedValue(new Error("network"));
    render(<HistoryScreen />);
    await waitFor(() =>
      expect(
        screen.getByText("履歴を取得できませんでした。時間をおいて再度お試しください。"),
      ).toBeInTheDocument(),
    );
  });
});
