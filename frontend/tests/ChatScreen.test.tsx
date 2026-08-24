import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { ChatScreen } from "@/components/ChatScreen";
import type { ChatThreadDetail, ChatThreadSummary } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const getChatThreadsMock = vi.fn();
const getChatThreadMock = vi.fn();
const postMessageMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getChatThreads: (...args: unknown[]) => getChatThreadsMock(...args),
  getChatThread: (...args: unknown[]) => getChatThreadMock(...args),
  postMessage: (...args: unknown[]) => postMessageMock(...args),
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

const THREAD_A: ChatThreadSummary = {
  thread_id: 42,
  question_id: "q_0001",
  question_title: "VPN移行の相談",
  counterpart: { id: "E001", name: "高梨 健太", dept: "技術部" },
  last_message: "承知しました",
  last_message_at: "2026-08-24T10:00:00",
  created_at: "2026-08-24T09:00:00",
};

const THREAD_B: ChatThreadSummary = {
  thread_id: 43,
  question_id: "q_0002",
  question_title: "UTMの設定について",
  counterpart: { id: "E002", name: "佐藤 花子", dept: "営業部" },
  last_message: null,
  last_message_at: null,
  created_at: "2026-08-23T09:00:00",
};

const DETAIL_A: ChatThreadDetail = {
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
    {
      id: 2,
      thread_id: 42,
      sender_id: "E001",
      body: "承知しました",
      created_at: "2026-08-24T10:00:00",
    },
  ],
};

beforeEach(() => {
  useCurrentUserMock.mockReset();
  getChatThreadsMock.mockReset();
  getChatThreadMock.mockReset();
  postMessageMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatScreen", () => {
  it("shows an empty state when there are no accepted threads", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([]);
    render(<ChatScreen />);

    await waitFor(() =>
      expect(screen.getByText("承諾済みの依頼がまだありません。")).toBeInTheDocument(),
    );
    expect(screen.getByText("左の一覧から会話を選択してください。")).toBeInTheDocument();
  });

  it("auto-selects the newest thread and shows its history, distinguishing sender/mine", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A, THREAD_B]);
    getChatThreadMock.mockResolvedValue(DETAIL_A);
    render(<ChatScreen />);

    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );
    expect(screen.getAllByText("高梨 健太").length).toBeGreaterThan(0);
    expect(screen.getByText("よろしくお願いします")).toBeInTheDocument();
    expect(screen.getByText("承知しました")).toBeInTheDocument(); // the bubble (list shows name + time only)
  });

  it("shows only the counterpart's name and timestamp in the list, not the question or preview", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A, THREAD_B]);
    getChatThreadMock.mockResolvedValue(DETAIL_A);
    render(<ChatScreen />);
    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );

    const list = within(screen.getByRole("list", { name: "チャットスレッド一覧" }));
    expect(list.getByText("2026-08-24 10:00")).toBeInTheDocument(); // THREAD_A's last_message_at
    expect(list.getByText("2026-08-23 09:00")).toBeInTheDocument(); // THREAD_B's created_at fallback
    expect(list.queryByText("VPN移行の相談")).not.toBeInTheDocument();
    expect(list.queryByText("UTMの設定について")).not.toBeInTheDocument();
    expect(list.queryByText("承知しました")).not.toBeInTheDocument(); // no last_message preview
  });

  it("switches threads on click", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A, THREAD_B]);
    getChatThreadMock.mockResolvedValue(DETAIL_A);
    render(<ChatScreen />);
    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );

    fireEvent.click(screen.getByText("佐藤 花子"));
    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(43, "E010", expect.anything()),
    );
  });

  it("opens the deep-linked thread from initialThreadId", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A, THREAD_B]);
    getChatThreadMock.mockResolvedValue(DETAIL_A);
    render(<ChatScreen initialThreadId="42" />);

    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );
  });

  it("sends a message from the composer and clears the input", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A]);
    getChatThreadMock.mockResolvedValue(DETAIL_A);
    postMessageMock.mockResolvedValue({
      id: 3,
      thread_id: 42,
      sender_id: "E010",
      body: "ありがとうございます",
      created_at: "2026-08-24T10:05:00",
    });
    render(<ChatScreen />);
    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );

    const textarea = screen.getByLabelText("メッセージを入力") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "ありがとうございます" } });
    fireEvent.click(screen.getByRole("button", { name: "送信" }));

    await waitFor(() =>
      expect(postMessageMock).toHaveBeenCalledWith({
        thread_id: 42,
        sender_id: "E010",
        body: "ありがとうございます",
      }),
    );
    await waitFor(() => expect(textarea.value).toBe(""));
  });

  it("disables the send button while the composer is empty", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A]);
    getChatThreadMock.mockResolvedValue(DETAIL_A);
    render(<ChatScreen />);
    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );

    expect(screen.getByRole("button", { name: "送信" })).toBeDisabled();
  });
});
