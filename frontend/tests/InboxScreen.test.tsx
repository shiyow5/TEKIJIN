import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { InboxScreen } from "@/components/InboxScreen";
import type { InboxItem } from "@/lib/api-types";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const getInboxMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getInbox: (...args: unknown[]) => getInboxMock(...args),
}));

function asUser(id: string | null, name?: string): CurrentUserContextValue {
  return {
    employees: [],
    currentUserId: id,
    currentUser: id && name ? { id, name, dept: "技術部" } : null,
    setCurrentUserId: vi.fn(),
    loading: false,
    error: false,
    reload: vi.fn(),
    canSwitch: false,
  };
}

const ITEM: InboxItem = {
  session_id: "sess-42",
  question_id: "api_q1",
  question: "UTM の移行時に気をつけることは？",
  topics: ["ネットワーク", "セキュリティ"],
  asker: { id: "E010", name: "藤田 悠斗", dept: "第3営業部" },
  created_at: "2026-08-23T09:30:00",
};

beforeEach(() => {
  useCurrentUserMock.mockReset();
  getInboxMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("InboxScreen", () => {
  it("shows loading until the current user resolves", () => {
    useCurrentUserMock.mockReturnValue(asUser(null));
    render(<InboxScreen />);
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(getInboxMock).not.toHaveBeenCalled();
  });

  it("fetches the inbox for the current user and links each item to its answer page", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001", "高梨 健太"));
    getInboxMock.mockResolvedValue([ITEM]);
    render(<InboxScreen />);

    await waitFor(() => expect(getInboxMock).toHaveBeenCalledWith("E001"));
    expect(screen.getByText("高梨 健太 さん宛てに届いた質問です。")).toBeInTheDocument();
    expect(screen.getByText("藤田 悠斗 さんからの質問")).toBeInTheDocument();
    expect(screen.getByText(ITEM.question)).toBeInTheDocument();
    expect(screen.getByText("ネットワーク")).toBeInTheDocument();
    expect(screen.getByText("2026-08-23 09:30")).toBeInTheDocument();

    const link = screen.getByRole("link", { name: /藤田 悠斗 さんからの質問/ });
    expect(link).toHaveAttribute("href", "/answer/sess-42");
  });

  it("shows an empty state when there are no pending handoffs", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E002", "佐藤 花子"));
    getInboxMock.mockResolvedValue([]);
    render(<InboxScreen />);

    await waitFor(() =>
      expect(screen.getByText("いまは届いている質問はありません。")).toBeInTheDocument(),
    );
  });

  it("shows an error state when the fetch fails", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001", "高梨 健太"));
    getInboxMock.mockRejectedValue(new Error("network"));
    render(<InboxScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("受信箱の取得に失敗しました");
  });

  it("falls back to 匿名 when the asker has no name", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E001", "高梨 健太"));
    getInboxMock.mockResolvedValue([{ ...ITEM, asker: { id: "E010" }, topics: [] }]);
    render(<InboxScreen />);

    await waitFor(() => expect(screen.getByText("匿名 さんからの質問")).toBeInTheDocument());
  });
});
