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
  },
  {
    source_id: "doc1",
    kind: "document",
    title: "社内Wi-Fi利用ガイド",
    topics: [],
    summary: "総務部の申請フォームから手続きできます。",
    responder_name: null,
    responder_department: null,
    resolved_at: "2026-08-21T10:00:00",
    question_id: null,
    session_id: null,
  },
];

const RESPONSE: KnowledgeListResponse = {
  items: ITEMS,
  total_matching: ITEMS.length,
  summary: { total_items: 42, self_resolution_rate: 0.25 },
};

beforeEach(() => {
  getKnowledgeListMock.mockReset();
  getKnowledgeListMock.mockResolvedValue(RESPONSE);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("KnowledgeScreen", () => {
  it("lists qa and document items with their own text, and the summary panel", async () => {
    render(<KnowledgeScreen />);

    expect(screen.getByRole("heading", { name: "ナレッジライブラリー" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /ホームへ戻る/ })).toHaveAttribute("href", "/");
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    expect(screen.getByText("社内Wi-Fi利用ガイド")).toBeInTheDocument();
    expect(
      screen.getByText("リダイレクトルールを事前に洗い出してから切り替えると安全です。"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("utm").length).toBeGreaterThan(0);
    // Both kinds show the same unified "更新日" label — no kind badge, no responder line.
    expect(screen.getByText("更新日: 2026-08-20")).toBeInTheDocument();
    expect(screen.getByText("更新日: 2026-08-21")).toBeInTheDocument();
    expect(screen.queryByText("Q&A")).not.toBeInTheDocument();
    expect(screen.queryByText("文書")).not.toBeInTheDocument();
    expect(screen.queryByText(/回答者:/)).not.toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
    // The per-responder panel was removed (PR #340 review) — that view is /dashboard's.
    expect(screen.queryByText("回答者別の件数")).not.toBeInTheDocument();
  });

  it("links a qa card to /knowledge/{source_id} and a document card to /documents/{source_id}", async () => {
    render(<KnowledgeScreen />);

    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /UTMの移行時の注意点/ })).toHaveAttribute(
      "href",
      "/knowledge/a1",
    );
    expect(screen.getByRole("link", { name: /社内Wi-Fi利用ガイド/ })).toHaveAttribute(
      "href",
      "/documents/doc1",
    );
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

  it("filters by department/topic/period via the form", async () => {
    render(<KnowledgeScreen />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    getKnowledgeListMock.mockClear();

    fireEvent.change(screen.getByLabelText("部署"), { target: { value: "マーケティング部" } });
    fireEvent.change(screen.getByLabelText("トピック"), { target: { value: "utm" } });
    fireEvent.change(screen.getByLabelText("期間（この日以降）"), {
      target: { value: "2026-08-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "検索" }));

    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(
        expect.objectContaining({
          department: "マーケティング部",
          topic: "utm",
          since: "2026-08-01",
        }),
      ),
    );

    // "条件をクリア" only appears once a filter is active, and resets the form.
    const clearButton = screen.getByRole("button", { name: "条件をクリア" });
    getKnowledgeListMock.mockClear();
    fireEvent.click(clearButton);

    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(
        expect.objectContaining({
          q: undefined,
          department: undefined,
          topic: undefined,
          since: undefined,
        }),
      ),
    );
    expect(screen.queryByRole("button", { name: "条件をクリア" })).not.toBeInTheDocument();
  });

  it("populates department/topic dropdown options from an initial unfiltered fetch", async () => {
    render(<KnowledgeScreen />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());

    const departmentSelect = screen.getByLabelText("部署") as HTMLSelectElement;
    const topicSelect = screen.getByLabelText("トピック") as HTMLSelectElement;
    await waitFor(() =>
      expect(Array.from(departmentSelect.options).map((o) => o.value)).toContain(
        "マーケティング部",
      ),
    );
    expect(Array.from(topicSelect.options).map((o) => o.value)).toContain("utm");
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
      summary: { total_items: 0, self_resolution_rate: 0 },
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
