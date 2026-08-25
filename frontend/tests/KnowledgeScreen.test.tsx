import { KnowledgeScreen } from "@/components/KnowledgeScreen";
import type { KnowledgeItem, KnowledgeListResponse } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getKnowledgeListMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getKnowledgeList: (...args: unknown[]) => getKnowledgeListMock(...args),
}));

const ITEMS: KnowledgeItem[] = [
  {
    question_id: "q1",
    title: "UTMの移行時の注意点",
    topics: ["marketing", "utm"],
    answer_body: "リダイレクトルールを事前に洗い出してから切り替えると安全です。",
    responder_name: "高梨 健太",
    responder_department: "マーケティング部",
    resolved_at: "2026-08-20T10:00:00",
    session_id: "sess-q1",
  },
  {
    question_id: "q2",
    title: "社内Wi-Fiの申請方法",
    topics: [],
    answer_body: "総務部の申請フォームから手続きできます。",
    responder_name: "田中 太郎",
    responder_department: "情報システム部",
    resolved_at: "2026-08-21T10:00:00",
    session_id: null,
  },
];

const RESPONSE: KnowledgeListResponse = {
  items: ITEMS,
  total_matching: ITEMS.length,
  summary: {
    total_items: 42,
    self_resolution_rate: 0.25,
    top_responders: [
      { employee_id: 5, name: "伊藤 健太", answer_count: 12 },
      { employee_id: 1, name: "田中 太郎", answer_count: 8 },
    ],
  },
};

beforeEach(() => {
  getKnowledgeListMock.mockReset();
  getKnowledgeListMock.mockResolvedValue(RESPONSE);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("KnowledgeScreen", () => {
  it("lists items with responder/department/topic/date, and the summary panel", async () => {
    render(<KnowledgeScreen />);

    expect(screen.getByRole("heading", { name: "ナレッジライブラリー" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
    expect(
      screen.getByText("リダイレクトルールを事前に洗い出してから切り替えると安全です。"),
    ).toBeInTheDocument();
    expect(screen.getByText("回答者: 高梨 健太（マーケティング部）")).toBeInTheDocument();
    expect(screen.getAllByText("utm").length).toBeGreaterThan(0);
    expect(screen.getByText("回答日: 2026-08-20")).toBeInTheDocument();

    // Side panels reuse the summary from the same response.
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.getByText("回答者別の件数")).toBeInTheDocument();
    expect(screen.getByText("伊藤 健太")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("hides the top-responders panel when there are none", async () => {
    getKnowledgeListMock.mockResolvedValue({
      items: ITEMS,
      total_matching: ITEMS.length,
      summary: { total_items: 2, self_resolution_rate: 0, top_responders: [] },
    });
    render(<KnowledgeScreen />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    expect(screen.queryByText("回答者別の件数")).not.toBeInTheDocument();
  });

  it("links a card with a session_id to /session/{id}, and leaves one without unlinked", async () => {
    render(<KnowledgeScreen />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    const link = screen.getByRole("link", { name: /UTMの移行時の注意点/ });
    expect(link).toHaveAttribute("href", "/session/sess-q1");
    expect(screen.queryByRole("link", { name: /社内Wi-Fiの申請方法/ })).not.toBeInTheDocument();
  });

  it("submits the search box as the q param", async () => {
    render(<KnowledgeScreen />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    getKnowledgeListMock.mockClear();

    fireEvent.change(screen.getByPlaceholderText("質問のキーワード"), {
      target: { value: "Wi-Fi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "検索" }));

    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(expect.objectContaining({ q: "Wi-Fi" })),
    );
  });

  it("clears the search back to the full list", async () => {
    render(<KnowledgeScreen />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    getKnowledgeListMock.mockClear();

    fireEvent.change(screen.getByPlaceholderText("質問のキーワード"), {
      target: { value: "Wi-Fi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "検索" }));
    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(expect.objectContaining({ q: "Wi-Fi" })),
    );

    // "条件をクリア" only appears once a search is active, and resets it.
    const clearButton = screen.getByRole("button", { name: "条件をクリア" });
    getKnowledgeListMock.mockClear();
    fireEvent.click(clearButton);

    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(expect.objectContaining({ q: undefined })),
    );
    expect(screen.queryByRole("button", { name: "条件をクリア" })).not.toBeInTheDocument();
  });

  it("paginates search results 8 at a time, but not the plain browse view", async () => {
    render(<KnowledgeScreen />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    // The unsearched browse view never paginates, even with more than one
    // page's worth of matches (total_matching from the seeded RESPONSE mock).
    expect(screen.queryByRole("button", { name: "次へ" })).not.toBeInTheDocument();

    getKnowledgeListMock.mockResolvedValue({
      items: ITEMS,
      total_matching: 12, // > RESULT_LIMIT (8) -> two pages
      summary: RESPONSE.summary,
    });
    getKnowledgeListMock.mockClear();
    fireEvent.change(screen.getByPlaceholderText("質問のキーワード"), {
      target: { value: "質問" },
    });
    fireEvent.click(screen.getByRole("button", { name: "検索" }));
    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(
        expect.objectContaining({ q: "質問", offset: 0, limit: 8 }),
      ),
    );

    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    const prevButton = screen.getByRole("button", { name: "前へ" });
    const nextButton = screen.getByRole("button", { name: "次へ" });
    expect(prevButton).toBeDisabled();
    expect(nextButton).not.toBeDisabled();

    getKnowledgeListMock.mockClear();
    fireEvent.click(nextButton);
    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(
        expect.objectContaining({ q: "質問", offset: 8, limit: 8 }),
      ),
    );
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "次へ" })).toBeDisabled();
  });

  it("shows an empty-state message when no items match", async () => {
    getKnowledgeListMock.mockResolvedValue({
      items: [],
      total_matching: 0,
      summary: { total_items: 0, self_resolution_rate: 0, top_responders: [] },
    });
    render(<KnowledgeScreen />);
    await waitFor(() =>
      expect(
        screen.getByText("条件に一致するナレッジが見つかりませんでした。"),
      ).toBeInTheDocument(),
    );
  });

  it("shows an error message when the fetch fails", async () => {
    getKnowledgeListMock.mockRejectedValue(new Error("network"));
    render(<KnowledgeScreen />);
    await waitFor(() =>
      expect(
        screen.getByText("ナレッジを取得できませんでした。時間をおいて再度お試しください。"),
      ).toBeInTheDocument(),
    );
  });
});
