import { ChatScreen } from "@/components/ChatScreen";
import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import type { ChatThreadDetail, ChatThreadSummary } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
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
  slack_channel_url: null,
};

beforeEach(() => {
  useCurrentUserMock.mockReset();
  replaceMock.mockReset();
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
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");

    await waitFor(() =>
      expect(screen.getByText("承諾済みの依頼がまだありません。")).toBeInTheDocument(),
    );
    expect(screen.getByText("一覧から会話を選んでください。")).toBeInTheDocument();
  });

  it("auto-selects the newest thread and shows its history, distinguishing sender/mine", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A, THREAD_B]);
    getChatThreadMock.mockResolvedValue(DETAIL_A);
    render(<ChatScreen />);

    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );
    // `findByText`, not `getByText`: the waitFor above only proves the fetch was
    // issued. Its result still has to land and re-render the conversation pane,
    // which until then shows 「読み込み中…」 — a synchronous getByText loses that
    // race intermittently under load (seen at ~25% of full-suite runs once #336
    // added a link to the list pane and shifted the timing).
    expect(await screen.findByText("よろしくお願いします")).toBeInTheDocument();
    expect(screen.getAllByText("高梨 健太").length).toBeGreaterThan(0);
    expect(screen.getByText("承知しました")).toBeInTheDocument(); // the bubble (list shows name + time only)
  });

  it("shows the counterpart, the question title and the timestamp — but no message preview", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A, THREAD_B]);
    getChatThreadMock.mockResolvedValue(DETAIL_A);
    render(<ChatScreen />);
    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );

    const list = within(screen.getByRole("list", { name: "チャットスレッド一覧" }));
    // Timestamps are naive-UTC on the wire; the JST display is +9h (#418).
    expect(list.getByText("2026-08-24 19:00")).toBeInTheDocument(); // THREAD_A's last_message_at
    expect(list.getByText("2026-08-23 18:00")).toBeInTheDocument(); // THREAD_B's created_at fallback
    // The title is what tells two threads with the SAME person apart; the
    // message preview stays out (it can carry content the list should not leak).
    expect(list.getByText("VPN移行の相談")).toBeInTheDocument();
    expect(list.getByText("UTMの設定について")).toBeInTheDocument();
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

  it("shows a per-thread Slack link when the pair has a shared channel", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A]);
    getChatThreadMock.mockResolvedValue({
      ...DETAIL_A,
      slack_channel_url: "https://slack.com/app_redirect?channel=C1&team=T1",
    });
    render(<ChatScreen />);

    const link = await screen.findByRole("link", { name: "Slackで開く" });
    expect(link).toHaveAttribute("href", "https://slack.com/app_redirect?channel=C1&team=T1");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("shows no per-thread Slack link when the pair has no shared channel yet", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getChatThreadsMock.mockResolvedValue([THREAD_A]);
    getChatThreadMock.mockResolvedValue(DETAIL_A); // slack_channel_url: null
    render(<ChatScreen />);

    await waitFor(() =>
      expect(getChatThreadMock).toHaveBeenCalledWith(42, "E010", expect.anything()),
    );
    expect(screen.queryByRole("link", { name: "Slackで開く" })).not.toBeInTheDocument();
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

  // --- Slack OAuth-result banner (#slack-integration) ------------------------- #
  describe("Slack OAuth result banner", () => {
    it("shows a success message and clears ?slack= from the URL when linked", async () => {
      useCurrentUserMock.mockReturnValue(asUser("E010"));
      getChatThreadsMock.mockResolvedValue([]);
      render(<ChatScreen initialSlackResult="linked" />);

      expect(await screen.findByText("Slackと連携しました。")).toBeInTheDocument();
      await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/chat"));
    });

    it("shows an error message when the Slack link failed", async () => {
      useCurrentUserMock.mockReturnValue(asUser("E010"));
      getChatThreadsMock.mockResolvedValue([]);
      render(<ChatScreen initialSlackResult="error" />);

      expect(
        await screen.findByText("Slack連携に失敗しました。時間をおいて再度お試しください。"),
      ).toBeInTheDocument();
    });

    it("preserves ?thread= when clearing the Slack result", async () => {
      useCurrentUserMock.mockReturnValue(asUser("E010"));
      getChatThreadsMock.mockResolvedValue([THREAD_A]);
      getChatThreadMock.mockResolvedValue(DETAIL_A);
      render(<ChatScreen initialThreadId="42" initialSlackResult="linked" />);

      await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/chat?thread=42"));
    });

    it("dismisses the banner on close", async () => {
      useCurrentUserMock.mockReturnValue(asUser("E010"));
      getChatThreadsMock.mockResolvedValue([]);
      render(<ChatScreen initialSlackResult="linked" />);

      const banner = await screen.findByText("Slackと連携しました。");
      fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
      expect(banner).not.toBeInTheDocument();
    });

    it("shows no banner when there is no Slack result", async () => {
      useCurrentUserMock.mockReturnValue(asUser("E010"));
      getChatThreadsMock.mockResolvedValue([]);
      render(<ChatScreen />);
      await waitFor(() =>
        expect(screen.getByText("承諾済みの依頼がまだありません。")).toBeInTheDocument(),
      );
      expect(screen.queryByText("Slackと連携しました。")).not.toBeInTheDocument();
      expect(replaceMock).not.toHaveBeenCalled();
    });
  });
});
