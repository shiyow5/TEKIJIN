import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { QuestionHistoryScreen } from "@/components/QuestionHistoryScreen";
import type { RecentQuestionItem } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const getRecentQuestionsMock = vi.fn();
const deleteQuestionMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getRecentQuestions: (...args: unknown[]) => getRecentQuestionsMock(...args),
  deleteQuestion: (...args: unknown[]) => deleteQuestionMock(...args),
  ApiError: class ApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
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
];

beforeEach(() => {
  useCurrentUserMock.mockReset();
  getRecentQuestionsMock.mockReset();
  deleteQuestionMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("QuestionHistoryScreen", () => {
  it("fetches with a generous limit so the full history is shown", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<QuestionHistoryScreen />);
    await waitFor(() =>
      expect(getRecentQuestionsMock).toHaveBeenCalledWith("E001", { limit: 200 }),
    );
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
  });

  it("shows an empty state when there is no history", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue([]);
    render(<QuestionHistoryScreen />);
    await waitFor(() => expect(screen.getByText("まだ質問はありません。")).toBeInTheDocument());
  });

  it("deletes a question and removes it from the list optimistically (#207/#F8)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    deleteQuestionMock.mockResolvedValue(undefined);
    render(<QuestionHistoryScreen />);
    await screen.findByText("UTMの移行時の注意点");

    const deleteButtons = screen.getAllByRole("button", { name: "削除する" });
    fireEvent.click(deleteButtons[0]);

    // Optimistic removal.
    expect(screen.queryByText("UTMの移行時の注意点")).not.toBeInTheDocument();
    await waitFor(() => expect(deleteQuestionMock).toHaveBeenCalledWith("q1", "E001"));
    // Still gone after the request resolves.
    expect(screen.queryByText("UTMの移行時の注意点")).not.toBeInTheDocument();
  });

  it("rolls back and shows a blocked message on a 409 (pending handoff)", async () => {
    const { ApiError } = await import("@/lib/api-client");
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    deleteQuestionMock.mockRejectedValue(new ApiError(409, "対応中"));
    render(<QuestionHistoryScreen />);
    await screen.findByText("UTMの移行時の注意点");

    fireEvent.click(screen.getAllByRole("button", { name: "削除する" })[0]);

    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent("対応中の依頼があるため");
    // Rolled back: the question reappears.
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
  });

  it("rolls back and shows a generic error on an unexpected failure", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    deleteQuestionMock.mockRejectedValue(new Error("network"));
    render(<QuestionHistoryScreen />);
    await screen.findByText("UTMの移行時の注意点");

    fireEvent.click(screen.getAllByRole("button", { name: "削除する" })[0]);

    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent("削除に失敗しました");
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
  });

  it("links a session_id back to /session/{id}, same as the recent-questions panel", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<QuestionHistoryScreen />);
    await screen.findByText("UTMの移行時の注意点");
    expect(screen.getByRole("link", { name: /「UTMの移行時の注意点」/ })).toHaveAttribute(
      "href",
      "/session/sess-q1",
    );
  });
});
