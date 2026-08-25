import { DocumentViewer } from "@/components/DocumentViewer";
import type { DocumentDetail } from "@/lib/api-types";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getDocumentMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getDocument: (...args: unknown[]) => getDocumentMock(...args),
}));

const DOC: DocumentDetail = {
  id: "doc_001",
  title: "社内IT手順書",
  body: "PCセットアップはキッティング手順書を用意する。",
  source: "社内Wiki/IT",
  updated_at: "2026-08-01T09:00:00",
};

beforeEach(() => getDocumentMock.mockReset());
afterEach(() => vi.restoreAllMocks());

describe("DocumentViewer", () => {
  it("shows loading, then the document's title, body and metadata", async () => {
    getDocumentMock.mockResolvedValue(DOC);
    render(<DocumentViewer docId="doc_001" />);

    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledWith("doc_001"));
    expect(screen.getByRole("heading", { name: "社内IT手順書" })).toBeInTheDocument();
    expect(screen.getByText(/キッティング手順書/)).toBeInTheDocument();
    expect(screen.getByText("出典: 社内Wiki/IT")).toBeInTheDocument();
    expect(screen.getByText("更新: 2026-08-01")).toBeInTheDocument();
  });

  it("shows a not-found state on a 404", async () => {
    getDocumentMock.mockRejectedValue({ status: 404 });
    render(<DocumentViewer docId="missing" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "文書が見つかりません" })).toBeInTheDocument(),
    );
  });

  it("shows a generic error on a non-404 failure", async () => {
    getDocumentMock.mockRejectedValue(new Error("network"));
    render(<DocumentViewer docId="doc_001" />);
    await waitFor(() => expect(screen.getByText(/文書を取得できませんでした/)).toBeInTheDocument());
  });

  it("links back to the originating session's result when fromSessionId is given (#179)", async () => {
    getDocumentMock.mockResolvedValue(DOC);
    render(<DocumentViewer docId="doc_001" fromSessionId="sess-9" />);
    const back = await screen.findByRole("link", { name: "結果へ戻る" });
    expect(back).toHaveAttribute("href", "/session/sess-9/result");
  });

  it("falls back to the question list when no originating session is known", async () => {
    getDocumentMock.mockResolvedValue(DOC);
    render(<DocumentViewer docId="doc_001" />);
    const back = await screen.findByRole("link", { name: "質問一覧へ戻る" });
    expect(back).toHaveAttribute("href", "/questions");
  });
});
