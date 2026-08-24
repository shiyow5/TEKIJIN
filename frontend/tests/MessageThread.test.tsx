import { MessageThread } from "@/components/MessageThread";
import type { MessageItem } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getMessagesMock = vi.fn();
const postMessageMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getMessages: (...args: unknown[]) => getMessagesMock(...args),
  postMessage: (...args: unknown[]) => postMessageMock(...args),
}));

const ITEMS: MessageItem[] = [
  { id: 1, sender_id: "E010", body: "よろしくお願いします", created_at: "2026-08-20T10:00:00" },
  { id: 2, sender_id: "E001", body: "承知しました", created_at: "2026-08-20T10:01:00" },
];

beforeEach(() => {
  getMessagesMock.mockReset();
  postMessageMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("MessageThread", () => {
  it("shows a loading state, then the thread once fetched", async () => {
    getMessagesMock.mockResolvedValue(ITEMS);
    render(<MessageThread sessionId="s1" currentUserId="E010" otherPartyName="高梨" />);
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    await waitFor(() => expect(getMessagesMock).toHaveBeenCalledWith("s1"));
    expect(await screen.findByText("よろしくお願いします")).toBeInTheDocument();
    expect(screen.getByText("承知しました")).toBeInTheDocument();
    expect(screen.getByText("高梨さんとのメッセージ")).toBeInTheDocument();
  });

  it("shows a generic heading when the other party's name is unknown", async () => {
    getMessagesMock.mockResolvedValue([]);
    render(<MessageThread sessionId="s1" currentUserId="E010" />);
    expect(screen.getByText("メッセージ")).toBeInTheDocument();
  });

  it("shows an empty state once loaded with no messages", async () => {
    getMessagesMock.mockResolvedValue([]);
    render(<MessageThread sessionId="s1" currentUserId="E010" />);
    expect(await screen.findByText("まだメッセージはありません。")).toBeInTheDocument();
  });

  it("distinguishes the viewer's own messages from the other party's", async () => {
    getMessagesMock.mockResolvedValue(ITEMS);
    render(<MessageThread sessionId="s1" currentUserId="E010" />);
    const own = await screen.findByText("よろしくお願いします");
    const other = screen.getByText("承知しました");
    expect(own.className).toContain("bg-primary");
    expect(other.className).toContain("bg-surface-container");
    expect(other.className).not.toContain("bg-primary");
  });

  it("sends a message and appends it to the thread", async () => {
    getMessagesMock.mockResolvedValue([]);
    postMessageMock.mockResolvedValue({
      id: 3,
      sender_id: "E010",
      body: "本文です",
      created_at: null,
    });
    render(<MessageThread sessionId="s1" currentUserId="E010" />);
    await screen.findByText("まだメッセージはありません。");

    fireEvent.change(screen.getByPlaceholderText("メッセージを入力…"), {
      target: { value: "本文です" },
    });
    fireEvent.click(screen.getByRole("button", { name: "送信" }));

    await waitFor(() =>
      expect(postMessageMock).toHaveBeenCalledWith({
        session_id: "s1",
        sender_id: "E010",
        body: "本文です",
      }),
    );
    expect(await screen.findByText("本文です")).toBeInTheDocument();
    // The input is cleared after a successful send.
    expect(screen.getByPlaceholderText("メッセージを入力…")).toHaveValue("");
  });

  it("disables sending a blank message", async () => {
    getMessagesMock.mockResolvedValue([]);
    render(<MessageThread sessionId="s1" currentUserId="E010" />);
    await screen.findByText("まだメッセージはありません。");
    expect(screen.getByRole("button", { name: "送信" })).toBeDisabled();
  });

  it("surfaces a retryable error when sending fails", async () => {
    getMessagesMock.mockResolvedValue([]);
    postMessageMock.mockRejectedValueOnce(new Error("boom"));
    render(<MessageThread sessionId="s1" currentUserId="E010" />);
    await screen.findByText("まだメッセージはありません。");

    fireEvent.change(screen.getByPlaceholderText("メッセージを入力…"), {
      target: { value: "本文です" },
    });
    fireEvent.click(screen.getByRole("button", { name: "送信" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("送信に失敗しました");
    // The draft text is preserved so the user can retry.
    expect(screen.getByPlaceholderText("メッセージを入力…")).toHaveValue("本文です");
  });

  it("polls again after the interval elapses", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getMessagesMock.mockResolvedValue([]);
    render(<MessageThread sessionId="s1" currentUserId="E010" />);
    await vi.waitFor(() => expect(getMessagesMock).toHaveBeenCalledTimes(1));

    await vi.advanceTimersByTimeAsync(4_000);
    expect(getMessagesMock).toHaveBeenCalledTimes(2);
  });
});
