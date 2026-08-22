import { AnswerScreen } from "@/components/AnswerScreen";
import type { HandoffResponse } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getHandoffMock = vi.fn();
const postAnswerMock = vi.fn();
const advanceSessionMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getHandoff: (...args: unknown[]) => getHandoffMock(...args),
  postAnswer: (...args: unknown[]) => postAnswerMock(...args),
  advanceSession: (...args: unknown[]) => advanceSessionMock(...args),
  ApiError: class ApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

const HANDOFF: HandoffResponse = {
  session_id: "s1",
  question: "UTM移行時の注意点について",
  asker: { id: "E010", name: "藤田 悠斗", dept: "第3営業部" },
  topics: ["ネットワーク・VPN"],
  products: ["UTM"],
  situation: "移行",
  missing: ["予算感"],
  responder: {
    person_id: "E001",
    name: "高梨 健太",
    dept: "技術部",
    score: 0.9,
    confidence: "高",
    reasons: [
      { type: "cert", detail: "情報処理安全確保支援士" },
      { type: "answers", detail: "類似の質問に過去5件回答（うち有用と評価3件）" },
    ],
  },
  draft: "高梨さんへの依頼文です。",
  reuse_count: 7,
  helpful_answer_count: 5,
};

const THREE_BUTTONS = ["回答する", "今は難しい", "別の人を薦める"];

describe("AnswerScreen", () => {
  beforeEach(() => {
    getHandoffMock.mockReset();
    postAnswerMock.mockReset();
    advanceSessionMock.mockReset();
    postAnswerMock.mockResolvedValue({ session_id: "s1", status: "resumed" });
    advanceSessionMock.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a loading placeholder before the handoff arrives", () => {
    getHandoffMock.mockReturnValue(new Promise(() => {})); // never resolves
    render(<AnswerScreen sessionId="s1" />);
    expect(screen.getByText(/読み込み中|準備中/)).toBeInTheDocument();
  });

  it("renders the question, asker, selection reasons and reuse count", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    render(<AnswerScreen sessionId="s1" />);

    expect(await screen.findByText("UTM移行時の注意点について")).toBeInTheDocument();
    expect(screen.getByText(/藤田 悠斗/)).toBeInTheDocument();
    expect(screen.getByText(/第3営業部/)).toBeInTheDocument();
    // selection reasons (verbatim details from the backend).
    expect(screen.getByText(/情報処理安全確保支援士/)).toBeInTheDocument();
    expect(screen.getByText(/類似の質問に過去5件回答/)).toBeInTheDocument();
    // draft (the filled-in 依頼文 the responder receives).
    expect(screen.getByText(/高梨さんへの依頼文です/)).toBeInTheDocument();
    // reuse count (the 見返り at the bottom).
    expect(screen.getByText(/7/)).toBeInTheDocument();
  });

  it("renders the three action buttons at an equal size (F-09: 断るを二級にしない)", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    render(<AnswerScreen sessionId="s1" />);
    await screen.findByText("UTM移行時の注意点について");

    const buttons = THREE_BUTTONS.map((name) => screen.getByRole("button", { name }));
    // Every button opts into the same equal-size contract, so none is demoted.
    for (const b of buttons) {
      expect(b).toHaveClass("min-h-[56px]");
      expect(b).toHaveClass("flex-1");
    }
    // The two decline options share identical classes (same visual weight).
    const [, defer, refer] = buttons;
    expect(refer.className).toBe(defer.className);
  });

  it("sends accepted and shows a confirmation when 回答する is clicked", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    render(<AnswerScreen sessionId="s1" />);
    await screen.findByText("UTM移行時の注意点について");

    fireEvent.click(screen.getByRole("button", { name: "回答する" }));

    await waitFor(() =>
      expect(postAnswerMock).toHaveBeenCalledWith({ session_id: "s1", outcome: "accepted" }),
    );
    expect(await screen.findByRole("heading", { name: /回答/ })).toBeInTheDocument();
  });

  it("sends declined when 今は難しい is clicked", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    render(<AnswerScreen sessionId="s1" />);
    await screen.findByText("UTM移行時の注意点について");

    fireEvent.click(screen.getByRole("button", { name: "今は難しい" }));

    await waitFor(() =>
      expect(postAnswerMock).toHaveBeenCalledWith({ session_id: "s1", outcome: "declined" }),
    );
  });

  it("sends declined when 別の人を薦める is clicked (interim mapping)", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    render(<AnswerScreen sessionId="s1" />);
    await screen.findByText("UTM移行時の注意点について");

    fireEvent.click(screen.getByRole("button", { name: "別の人を薦める" }));

    await waitFor(() =>
      expect(postAnswerMock).toHaveBeenCalledWith({ session_id: "s1", outcome: "declined" }),
    );
  });

  it("shows a 'gone' message when the session is no longer awaiting a response", async () => {
    const { ApiError } = await import("@/lib/api-client");
    getHandoffMock.mockRejectedValue(new ApiError(404, "no responder handoff"));
    render(<AnswerScreen sessionId="s1" />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "回答する" })).toBeNull();
  });

  it("shows a load-failure message (not 'gone') for a generic load error", async () => {
    getHandoffMock.mockRejectedValue(new Error("network"));
    render(<AnswerScreen sessionId="s1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/読み込みに失敗/);
  });

  it("handles a handoff with no responder, empty slots and no reasons", async () => {
    getHandoffMock.mockResolvedValue({
      ...HANDOFF,
      asker: { id: "E010", name: null, dept: null },
      topics: [],
      products: [],
      situation: null,
      missing: [],
      responder: null,
      reuse_count: 0,
      helpful_answer_count: 0,
    } satisfies HandoffResponse);
    render(<AnswerScreen sessionId="s1" />);

    expect(await screen.findByText("UTM移行時の注意点について")).toBeInTheDocument();
    expect(screen.getByText("質問者さん")).toBeInTheDocument(); // name fallback
    expect(screen.getByText("根拠を確認中…")).toBeInTheDocument(); // empty reasons
    // still fully actionable even without candidate metadata.
    expect(screen.getByRole("button", { name: "回答する" })).toBeEnabled();
  });

  it("surfaces a retryable error when the outcome submit fails", async () => {
    getHandoffMock.mockResolvedValue(HANDOFF);
    postAnswerMock.mockRejectedValueOnce(new Error("network"));
    render(<AnswerScreen sessionId="s1" />);
    await screen.findByText("UTM移行時の注意点について");

    fireEvent.click(screen.getByRole("button", { name: "回答する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/失敗|お試し/);
    // still interactive (retryable), not dead-ended.
    expect(screen.getByRole("button", { name: "回答する" })).toBeEnabled();
  });
});
