import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { RecentQuestions } from "@/components/RecentQuestions";
import type { RecentQuestionItem } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  deleteQuestionMock.mockReset();
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
    // `findBy` for the first assertion: the waitFor above proves only that the
    // fetch was issued, not that its result rendered.
    expect(await screen.findByText("UTMの移行時の注意点")).toBeInTheDocument();
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
    expect(screen.getByRole("link", { name: /「UTMの移行時の注意点」/ })).toHaveAttribute(
      "href",
      "/session/sess-q1",
    );
    expect(screen.getByRole("link", { name: /「社内Wi-Fiの申請方法」/ })).toHaveAttribute(
      "href",
      "/session/sess-q2",
    );
    // q3 has no session_id (seeded history) -> not a link.
    expect(
      screen.queryByRole("link", { name: /「社内PCのセットアップ手順」/ }),
    ).not.toBeInTheDocument();
    // ...and it is marked 「履歴のみ」 so it does not look pressable-but-dead (#179).
    expect(screen.getByText("履歴のみ")).toBeInTheDocument();
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

  // --- #207: delete a past question ---------------------------------------- //
  it("deletes a question after inline confirmation and drops it from the list", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    deleteQuestionMock.mockResolvedValue({ question_id: "q1", deleted: true });
    render(<RecentQuestions />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    // First click only asks for confirmation — no delete call yet (not undoable).
    fireEvent.click(screen.getByRole("button", { name: "「UTMの移行時の注意点」を削除" }));
    expect(deleteQuestionMock).not.toHaveBeenCalled();
    expect(screen.getByText("削除しますか？")).toBeInTheDocument();

    // Confirm -> deletes by question_id and removes the card.
    fireEvent.click(screen.getByRole("button", { name: "削除" }));
    await waitFor(() => expect(deleteQuestionMock).toHaveBeenCalledWith("q1"));
    await waitFor(() => expect(screen.queryByText("UTMの移行時の注意点")).not.toBeInTheDocument());
    // The other questions are untouched.
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
  });

  it("cancels the delete when 「やめる」 is chosen (item stays)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<RecentQuestions />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    const trigger = screen.getByRole("button", { name: "「UTMの移行時の注意点」を削除" });
    // fireEvent.click does not focus the element the way a real click does, so
    // focus it explicitly to exercise the dialog's opener-restore behavior.
    trigger.focus();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(deleteQuestionMock).not.toHaveBeenCalled();
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
    // Focus returns to the ✕ button that opened the dialog (#286 a11y requirement).
    expect(trigger).toHaveFocus();
  });

  // --- #286: modal confirmation (dialog semantics, title, Esc, backdrop) --- //
  it("shows the target question's title inside a modal dialog", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<RecentQuestions />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "「UTMの移行時の注意点」を削除" }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByText(/UTMの移行時の注意点/)).toBeInTheDocument();
  });

  it("cancels the delete on Escape", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<RecentQuestions />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "「UTMの移行時の注意点」を削除" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(deleteQuestionMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancels the delete on a backdrop click, but not on a click inside the dialog", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    render(<RecentQuestions />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "「UTMの移行時の注意点」を削除" }));
    const dialog = screen.getByRole("dialog");

    // A click inside the dialog panel must not close it.
    fireEvent.click(dialog);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // The backdrop (the dialog's overlay parent) closes it.
    fireEvent.click(dialog.parentElement as HTMLElement);
    expect(deleteQuestionMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("suppresses every dismissal path while the delete is in flight", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    // Never settles: the component stays in its "deleting" phase for the whole test.
    deleteQuestionMock.mockReturnValue(new Promise(() => {}));
    render(<RecentQuestions />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "「UTMの移行時の注意点」を削除" }));
    fireEvent.click(screen.getByRole("button", { name: "削除" }));
    await waitFor(() => expect(deleteQuestionMock).toHaveBeenCalledWith("q1"));

    // The request is already sent, so closing the dialog would only hide it.
    // Escape, the backdrop and 「やめる」 all have to be inert here.
    const dialog = screen.getByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(dialog.parentElement as HTMLElement);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(deleteQuestionMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the question and flags an error when the delete fails", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001"));
    getRecentQuestionsMock.mockResolvedValue(ITEMS);
    deleteQuestionMock.mockRejectedValue(new Error("boom"));
    render(<RecentQuestions />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "「UTMの移行時の注意点」を削除" }));
    fireEvent.click(screen.getByRole("button", { name: "削除" }));
    await waitFor(() => expect(deleteQuestionMock).toHaveBeenCalledWith("q1"));
    // The card is still there (delete failed) and the control shows the error marker.
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "「UTMの移行時の注意点」を削除" }),
      ).toHaveTextContent("!"),
    );
  });
});
