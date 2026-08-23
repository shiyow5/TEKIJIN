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
    session_id: "sess-q2",
    created_at: "2026-08-21T10:00:00",
  },
  {
    question_id: "q3",
    title: "社内PCのセットアップ手順",
    resolved: true,
    resolution: "document",
    responder_name: null,
    session_id: null, // seeded history: no live session -> not clickable
    created_at: "2026-08-22T10:00:00",
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
    // Two items are resolved (person + document), so there are two 解決済 chips.
    expect(screen.getAllByText("解決済")).toHaveLength(2);
    expect(screen.getByText("高梨 健太")).toBeInTheDocument();
    // Unresolved item: 対応中 + a "adjusting" note instead of a responder.
    expect(screen.getByText("対応中")).toBeInTheDocument();
    expect(screen.getByText("取り次ぎ先を調整中です。")).toBeInTheDocument();
    // Document-route item is self-resolved: shows a document note, not "adjusting" (#142).
    expect(screen.getByText("社内文書で回答")).toBeInTheDocument();
    expect(screen.getByText("社内PCのセットアップ手順")).toBeInTheDocument();
  });

  it("links questions with a session_id to their session for re-viewing (#150)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<RecentQuestions />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    // q1 / q2 have a session_id -> clickable link to /session/{id}.
    expect(
      screen.getByRole("link", { name: /「UTMの移行時の注意点」の結果をもう一度見る/ }),
    ).toHaveAttribute("href", "/session/sess-q1");
    expect(
      screen.getByRole("link", { name: /「社内Wi-Fiの申請方法」の結果をもう一度見る/ }),
    ).toHaveAttribute("href", "/session/sess-q2");
    // q3 has no session_id (seeded history) -> not a link.
    expect(
      screen.queryByRole("link", { name: /「社内PCのセットアップ手順」/ }),
    ).not.toBeInTheDocument();
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
