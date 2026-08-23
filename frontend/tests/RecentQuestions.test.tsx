import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { RecentQuestions } from "@/components/RecentQuestions";
import type { RecentQuestionItem } from "@/lib/api-types";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const getRecentQuestionsMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getRecentQuestions: (...args: unknown[]) => getRecentQuestionsMock(...args),
}));

function asUser(id: string | null): CurrentUserContextValue {
  return {
    employees: [],
    currentUserId: id,
    currentUser: null,
    setCurrentUserId: vi.fn(),
    loading: false,
  };
}

const ITEMS: RecentQuestionItem[] = [
  {
    question_id: "q1",
    title: "UTMの移行時の注意点",
    resolved: true,
    responder_name: "高梨 健太",
    created_at: "2026-08-20T10:00:00",
  },
  {
    question_id: "q2",
    title: "社内Wi-Fiの申請方法",
    resolved: false,
    responder_name: null,
    created_at: "2026-08-21T10:00:00",
  },
];

beforeEach(() => {
  useCurrentUserMock.mockReset();
  getRecentQuestionsMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RecentQuestions", () => {
  it("shows loading until the current user resolves", () => {
    useCurrentUserMock.mockReturnValue(asUser(null));
    render(<RecentQuestions />);
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(getRecentQuestionsMock).not.toHaveBeenCalled();
  });

  it("fetches the current user's questions and shows status + responder", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<RecentQuestions />);

    await waitFor(() => expect(getRecentQuestionsMock).toHaveBeenCalledWith("E001"));
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
    expect(screen.getByText("解決済")).toBeInTheDocument();
    expect(screen.getByText("高梨 健太")).toBeInTheDocument();
    // Unresolved item: 対応中 + a "adjusting" note instead of a responder.
    expect(screen.getByText("対応中")).toBeInTheDocument();
    expect(screen.getByText("取り次ぎ先を調整中です。")).toBeInTheDocument();
  });

  it("shows an empty state when there is no history", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E002"));
    getRecentQuestionsMock.mockResolvedValue([]);
    render(<RecentQuestions />);
    await waitFor(() => expect(screen.getByText("まだ質問はありません。")).toBeInTheDocument());
  });

  it("shows an error note when the fetch fails", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockRejectedValue(new Error("network"));
    render(<RecentQuestions />);
    await waitFor(() =>
      expect(
        screen.getByText("履歴を取得できませんでした。時間をおいて再度お試しください。"),
      ).toBeInTheDocument(),
    );
  });
});
