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
  {
    source_id: "ku_7",
    kind: "knowledge",
    title: "CRMを導入したが現場が入力せず定着しない",
    topics: ["CRM・営業支援"],
    summary: "打ち手: 入力項目を5つに絞る\n結果: 定着した",
    responder_name: null,
    responder_department: null,
    resolved_at: "2026-08-22T10:00:00",
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

  it("renders a knowledge unit (#533) as a non-linked card with a ナレッジ marker", async () => {
    render(<KnowledgeScreen />);

    await waitFor(() =>
      expect(screen.getByText("CRMを導入したが現場が入力せず定着しない")).toBeInTheDocument(),
    );
    // A distilled knowledge unit is marked and shown non-linked (no per-unit detail viewer).
    expect(screen.getByText("ナレッジ")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /CRMを導入したが現場が入力せず定着しない/ }),
    ).not.toBeInTheDocument();
  });

  it("submits the search box as the q param", async () => {
    render(<KnowledgeScreen />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    getKnowledgeListMock.mockClear();

    fireEvent.change(screen.getByPlaceholderText(/キーワードで検索/), {
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

    fireEvent.change(screen.getByPlaceholderText(/キーワードで検索/), {
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

  it("paginates 5 at a time, browsing included", async () => {
    getKnowledgeListMock.mockResolvedValue({
      items: ITEMS,
      total_matching: 12, // > RESULT_LIMIT (5) -> three pages
      summary: RESPONSE.summary,
    });
    render(<KnowledgeScreen />);
    await waitFor(() => expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument());
    // Browsing paginates too: gating this on an active filter left everything
    // past the newest page unreachable for a reader with no keyword in mind.
    expect(await screen.findByText("1 / 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "次へ" })).toBeInTheDocument();

    getKnowledgeListMock.mockClear();
    fireEvent.change(screen.getByPlaceholderText(/キーワードで検索/), {
      target: { value: "質問" },
    });
    fireEvent.click(screen.getByRole("button", { name: "検索" }));
    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(
        expect.objectContaining({ q: "質問", offset: 0, limit: 5 }),
      ),
    );

    // `findBy`: the waitFor above proves only that the fetch was issued.
    expect(await screen.findByText("1 / 3")).toBeInTheDocument();
    const prevButton = screen.getByRole("button", { name: "前へ" });
    const nextButton = screen.getByRole("button", { name: "次へ" });
    expect(prevButton).toBeDisabled();
    expect(nextButton).not.toBeDisabled();

    getKnowledgeListMock.mockClear();
    fireEvent.click(nextButton);
    await waitFor(() =>
      expect(getKnowledgeListMock).toHaveBeenCalledWith(
        expect.objectContaining({ q: "質問", offset: 5, limit: 5 }),
      ),
    );
    expect(await screen.findByText("2 / 3")).toBeInTheDocument();
    // Page 2 of 3 — still more to go, so 次へ stays live and 前へ wakes up.
    expect(screen.getByRole("button", { name: "次へ" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "前へ" })).not.toBeDisabled();
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
