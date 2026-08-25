import { KnowledgeDetailScreen } from "@/components/KnowledgeDetailScreen";
import type { KnowledgeItem } from "@/lib/api-types";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getKnowledgeDetailMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getKnowledgeDetail: (...args: unknown[]) => getKnowledgeDetailMock(...args),
}));

const ITEM: KnowledgeItem = {
  source_id: "a1",
  kind: "qa",
  title: "UTMの移行時の注意点",
  topics: ["marketing", "utm"],
  summary: "リダイレクトルールを事前に洗い出してから切り替えると安全です。",
  responder_name: "高梨 健太",
  responder_department: "マーケティング部",
  resolved_at: "2026-08-20T10:00:00",
  question_id: "q1",
  session_id: "sess-q1",
};

beforeEach(() => {
  getKnowledgeDetailMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("KnowledgeDetailScreen", () => {
  it("renders the full question/answer, date, topics, and a session link — no responder", async () => {
    getKnowledgeDetailMock.mockResolvedValue(ITEM);
    render(<KnowledgeDetailScreen sourceId="a1" />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    expect(getKnowledgeDetailMock).toHaveBeenCalledWith("a1");
    expect(
      screen.getByText("リダイレクトルールを事前に洗い出してから切り替えると安全です。"),
    ).toBeInTheDocument();
    expect(screen.getByText("更新日: 2026-08-20")).toBeInTheDocument();
    expect(screen.getByText("utm")).toBeInTheDocument();
    expect(screen.queryByText(/高梨 健太/)).not.toBeInTheDocument();
    expect(screen.queryByText(/回答者:/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /セッション結果を見る/ })).toHaveAttribute(
      "href",
      "/session/sess-q1",
    );
    expect(screen.getByRole("link", { name: /ナレッジライブラリーへ戻る/ })).toHaveAttribute(
      "href",
      "/knowledge",
    );
  });

  it("renders gracefully when topics/date/session are all absent", async () => {
    getKnowledgeDetailMock.mockResolvedValue({
      ...ITEM,
      topics: [],
      resolved_at: null,
      summary: "",
      session_id: null,
    });
    render(<KnowledgeDetailScreen sourceId="a1" />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    expect(screen.queryByText(/更新日:/)).not.toBeInTheDocument();
    expect(screen.queryByText("utm")).not.toBeInTheDocument();
    expect(screen.getByText("（回答本文はありません）")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /セッション結果を見る/ })).not.toBeInTheDocument();
  });

  it("omits the session link when there is no session_id", async () => {
    getKnowledgeDetailMock.mockResolvedValue({ ...ITEM, session_id: null });
    render(<KnowledgeDetailScreen sourceId="a1" />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: /セッション結果を見る/ })).not.toBeInTheDocument();
  });

  it("shows a not-found message on a 404", async () => {
    getKnowledgeDetailMock.mockRejectedValue(
      Object.assign(new Error("not found"), { status: 404 }),
    );
    render(<KnowledgeDetailScreen sourceId="missing" />);
    await waitFor(() => expect(screen.getByText("ナレッジが見つかりません")).toBeInTheDocument());
  });

  it("shows a generic error message on any other failure", async () => {
    getKnowledgeDetailMock.mockRejectedValue(new Error("network"));
    render(<KnowledgeDetailScreen sourceId="a1" />);
    await waitFor(() =>
      expect(
        screen.getByText("ナレッジを取得できませんでした。時間をおいて再度お試しください。"),
      ).toBeInTheDocument(),
    );
  });
});
