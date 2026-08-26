import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { HistoryScreen } from "@/components/HistoryScreen";
import type { RecentQuestionItem } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

/** Six items — one more than a page (#397's page size is 5) — for pager tests. */
function makeManyItems(count: number): RecentQuestionItem[] {
  return Array.from({ length: count }, (_, i) => ({
    question_id: `q${i + 1}`,
    title: `質問${i + 1}`,
    resolved: false,
    resolution: "pending",
    responder_name: null,
    session_id: null,
    created_at: "2026-08-19T09:30:00",
  }));
}

/** Opens the "…" options menu for the row whose title is `title`. */
function openOptionsMenu(title: string) {
  fireEvent.click(screen.getByRole("button", { name: `「${title}」の操作` }));
}

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
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(getRecentQuestionsMock).not.toHaveBeenCalled();
  });

  it("fetches the full history (limit 200) and renders each row as a whole-card link (#397)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() =>
      expect(getRecentQuestionsMock).toHaveBeenCalledWith("E001", { limit: 200 }),
    );
    // `findBy` for the first assertion: the waitFor above proves only that the
    // fetch was issued, not that its result rendered.
    expect(await screen.findByText("UTMの移行時の注意点")).toBeInTheDocument();
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
    // Naive-UTC on the wire -> JST display is +9h (#418).
    expect(screen.getByText("2026-08-20 19:00")).toBeInTheDocument();
    expect(screen.getByText("回答者: 高梨 健太")).toBeInTheDocument();
    expect(screen.getByText("取り次ぎ先を調整中")).toBeInTheDocument();

    // The whole card is the click target when a session is replayable — no
    // separate "結果を見る" text link exists anymore (#397).
    expect(screen.queryByText("結果を見る")).not.toBeInTheDocument();
    const sessionLink = screen.getByText("UTMの移行時の注意点").closest("a");
    // `?from=history` (#397 follow-up) lets the destination send the user back
    // to their history list instead of the home hub.
    expect(sessionLink).toHaveAttribute("href", "/session/sess-q1?from=history");

    // A history-only row (no session_id) stays non-interactive and is marked as such.
    expect(screen.getByText("社内Wi-Fiの申請方法").closest("a")).toBeNull();
    expect(screen.getByText("履歴のみ")).toBeInTheDocument();
  });

  it("deletes a question via the options menu after confirmation and removes it from the list", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    deleteQuestionMock.mockResolvedValue({ question_id: "q2", deleted: true });
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    openOptionsMenu("社内Wi-Fiの申請方法");
    fireEvent.click(screen.getByRole("menuitem", { name: "削除" }));
    const dialog = screen.getByRole("dialog", { name: "削除しますか？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "削除" }));

    await waitFor(() => expect(deleteQuestionMock).toHaveBeenCalledWith("q2"));
    await waitFor(() => expect(screen.queryByText("社内Wi-Fiの申請方法")).not.toBeInTheDocument());
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
  });

  it("shows an error and keeps the row when delete fails", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    deleteQuestionMock.mockRejectedValueOnce(new Error("boom"));
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    openOptionsMenu("社内Wi-Fiの申請方法");
    fireEvent.click(screen.getByRole("menuitem", { name: "削除" }));
    const dialog = screen.getByRole("dialog", { name: "削除しますか？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "削除" }));

    await waitFor(() => expect(deleteQuestionMock).toHaveBeenCalledWith("q2"));
    await waitFor(() =>
      expect(screen.getByText("削除に失敗しました。もう一度お試しください。")).toBeInTheDocument(),
    );
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
  });

  it("marks a pending question self-resolved via the options menu after confirmation (#397)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    // The pending item offers a self-resolve option; the resolved one only offers 削除.
    expect(screen.getByText("取り次ぎ先を調整中")).toBeInTheDocument();
    openOptionsMenu("社内Wi-Fiの申請方法");
    expect(screen.queryByRole("menuitem", { name: "自分で解決した" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "自分で解決した" }));

    const dialog = screen.getByRole("dialog", { name: "自分で解決しましたか？" });
    expect(dialog).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "解決済みにする" }));

    await waitFor(() => expect(resolveQuestionMock).toHaveBeenCalledWith("q2"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("自分で解決")).toBeInTheDocument();
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
  });

  it("does not offer 自分で解決した for an already-resolved question", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    openOptionsMenu("UTMの移行時の注意点");
    expect(screen.queryByRole("menuitem", { name: "自分で解決した" })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "削除" })).toBeInTheDocument();
  });

  it("returns focus to the … trigger after cancelling delete or resolve", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    const trigger = screen.getByRole("button", { name: "「社内Wi-Fiの申請方法」の操作" });

    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("menuitem", { name: "削除" }));
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(trigger).toHaveFocus();

    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("menuitem", { name: "自分で解決した" }));
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(trigger).toHaveFocus();
  });

  it("closes the self-resolve popup without resolving when cancelled (#289)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    openOptionsMenu("社内Wi-Fiの申請方法");
    fireEvent.click(screen.getByRole("menuitem", { name: "自分で解決した" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(resolveQuestionMock).not.toHaveBeenCalled();
    expect(screen.getByText("取り次ぎ先を調整中")).toBeInTheDocument();
  });

  it("closes the self-resolve popup on Escape without resolving (#289)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    openOptionsMenu("社内Wi-Fiの申請方法");
    fireEvent.click(screen.getByRole("menuitem", { name: "自分で解決した" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(resolveQuestionMock).not.toHaveBeenCalled();
  });

  // `ModalDialog` gained backdrop-click dismissal for the delete confirmation
  // (#286), where cancelling costs nothing. It is opt-in precisely so it does
  // not spread to dialogs like this one by default.
  it("does not dismiss the self-resolve popup on a backdrop click", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    openOptionsMenu("社内Wi-Fiの申請方法");
    fireEvent.click(screen.getByRole("menuitem", { name: "自分で解決した" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(dialog.parentElement as HTMLElement);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(resolveQuestionMock).not.toHaveBeenCalled();
  });

  it("keeps a pending question and shows an error when self-resolve fails (#159)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    resolveQuestionMock.mockRejectedValueOnce(new Error("boom"));
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument());
    openOptionsMenu("社内Wi-Fiの申請方法");
    fireEvent.click(screen.getByRole("menuitem", { name: "自分で解決した" }));
    fireEvent.click(screen.getByRole("button", { name: "解決済みにする" }));

    await waitFor(() => expect(resolveQuestionMock).toHaveBeenCalledWith("q2"));
    // The row stays pending and surfaces an error, rather than being lost.
    await waitFor(() =>
      expect(
        screen.getByText("解決の記録に失敗しました。もう一度お試しください。"),
      ).toBeInTheDocument(),
    );
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

  it("paginates the list 5 items per page and does not show a pager for a short list (#397)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "次へ" })).not.toBeInTheDocument();
  });

  it("moves between pages of 5 with 前へ/次へ, disabling at each boundary (#397)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(makeManyItems(6));
    render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("質問1")).toBeInTheDocument());
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByText("質問5")).toBeInTheDocument();
    expect(screen.queryByText("質問6")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "前へ" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "次へ" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByText("質問6")).toBeInTheDocument();
    expect(screen.queryByText("質問1")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "次へ" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "前へ" }));
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByText("質問1")).toBeInTheDocument();
  });

  it("resets to page 1 when the acting user changes", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(makeManyItems(6));
    const { rerender } = render(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("質問1")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "次へ" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();

    useCurrentUserMock.mockReturnValue(asUser("E002"));
    getRecentQuestionsMock.mockResolvedValue(makeManyItems(6));
    rerender(<HistoryScreen />);

    await waitFor(() => expect(screen.getByText("1 / 2")).toBeInTheDocument());
    expect(screen.getByText("質問1")).toBeInTheDocument();
  });
});
