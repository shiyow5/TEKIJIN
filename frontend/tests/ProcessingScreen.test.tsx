import { ProcessingScreen } from "@/components/ProcessingScreen";
import { SessionStreamProvider } from "@/components/SessionStreamProvider";
import type { EventStreamState, StreamEvent } from "@/hooks/useEventStream";
import { ApiError } from "@/lib/api-client";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const postAnswerMock = vi.fn();
const requestDocumentFallbackMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  postAnswer: (...args: unknown[]) => postAnswerMock(...args),
  requestDocumentFallback: (...args: unknown[]) => requestDocumentFallbackMock(...args),
  // Real-shaped ApiError so `err instanceof ApiError` + `.status` work.
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message?: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

function state(partial: Partial<EventStreamState>): EventStreamState {
  return { events: [], terminal: false, ...partial };
}

function renderScreen(stream: EventStreamState) {
  return render(<ProcessingScreen sessionId="abc-123" streamState={stream} />);
}

describe("ProcessingScreen", () => {
  beforeEach(() => {
    pushMock.mockReset();
    postAnswerMock.mockReset();
    postAnswerMock.mockResolvedValue({ session_id: "abc-123", status: "accepted" });
    requestDocumentFallbackMock.mockReset();
    requestDocumentFallbackMock.mockResolvedValue({
      session_id: "abc-123",
      status: "handoff_queued",
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("shows an in-progress step while no events have arrived yet", () => {
    renderScreen(state({}));
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");
    expect(screen.getByText("最適な回答者を探しています…")).toBeInTheDocument();
    expect(screen.getByTestId("active-step")).toBeInTheDocument();
  });

  it("renders the understood step with domain, situation and confidence", () => {
    renderScreen(
      state({
        understood: {
          topics: ["ネットワーク"],
          products: ["UTM"],
          situation: "他社製品からの移行",
          question_type: "how",
          confidence: 0.85,
        },
      }),
    );
    expect(screen.getByText("質問を理解しました")).toBeInTheDocument();
    expect(screen.getByText("領域: ネットワーク / UTM")).toBeInTheDocument();
    expect(screen.getByText("状況: 他社製品からの移行")).toBeInTheDocument();
    expect(screen.getByText("AIの解釈確信度 85%")).toBeInTheDocument();
  });

  it("renders the route step with a labelled route (not the raw enum) and confidence", () => {
    renderScreen(
      state({ route: { route: "person", reason: "詳しい人がいます", confidence: 0.7 } }),
    );
    expect(screen.getByText("回答の経路を判断しました")).toBeInTheDocument();
    // The raw enum "person" must not leak; it is rendered as a Japanese label.
    expect(screen.getByText("経路: 人に聞く")).toBeInTheDocument();
    expect(screen.queryByText("経路: person")).not.toBeInTheDocument();
    expect(screen.getByText("AIの解釈確信度 70%")).toBeInTheDocument();
  });

  it("renders the recommend step and a link to the result screen", () => {
    renderScreen(
      state({
        recommend: {
          recommendations: [
            { person_id: "E001", name: "高梨", score: 0.9, confidence: "high", reasons: [] },
            { person_id: "E002", name: "鈴木", score: 0.8, confidence: "mid", reasons: [] },
          ],
        },
      }),
    );
    expect(screen.getByText("候補を2名見つけました")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "結果を見る" }));
    expect(pushMock).toHaveBeenCalledWith("/session/abc-123/result");
  });

  it("renders the additive cited answer alongside the live flow (#413)", () => {
    renderScreen(
      state({
        route: { route: "person", reason: "詳しい人がいます", confidence: 0.8 },
        reference: {
          answer: "過去の類似回答です。",
          citations: [{ source_id: "qa_7", kind: "qa" }],
        },
      }),
    );
    expect(screen.getByText("参考: 過去の類似回答")).toBeInTheDocument();
    expect(screen.getByText("過去の類似回答です。")).toBeInTheDocument();
    expect(screen.getByText("過去の回答 qa_7")).toBeInTheDocument();
  });

  it("omits the reference block when no additive answer arrived (#413)", () => {
    renderScreen(state({ route: { route: "person", reason: "", confidence: 0.8 } }));
    expect(screen.queryByText("参考: 過去の類似回答")).not.toBeInTheDocument();
  });

  it("stops the active-step spinner once a result is ready (person route pauses, #148)", () => {
    // The person route pauses at `send` (non-terminal) after recommend/draft — the
    // spinner must not keep spinning once the candidate + draft are ready.
    renderScreen(
      state({
        recommend: {
          recommendations: [
            { person_id: "E001", name: "高梨", score: 0.9, confidence: "high", reasons: [] },
          ],
        },
        draft: { draft: "高梨さんへの依頼文" },
      }),
    );
    expect(screen.getByText("回答者が見つかりました")).toBeInTheDocument();
    expect(screen.queryByTestId("active-step")).not.toBeInTheDocument();
    expect(screen.queryByText("分析を続けています…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "結果を見る" })).toBeInTheDocument();
  });

  it("keeps the spinner while only understood/route have arrived (still analyzing)", () => {
    renderScreen(
      state({ route: { route: "person", reason: "詳しい人がいます", confidence: 0.7 } }),
    );
    expect(screen.getByTestId("active-step")).toBeInTheDocument();
  });

  it("submits a followup reply via postAnswer and hides the form", async () => {
    renderScreen(state({ followup: { question: "製品名を教えてください", missing: ["product"] } }));

    expect(screen.getByText("製品名を教えてください")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("補足の回答"), { target: { value: "  Fortinet  " } });
    fireEvent.click(screen.getByRole("button", { name: "回答する" }));

    await waitFor(() =>
      expect(postAnswerMock).toHaveBeenCalledWith({ session_id: "abc-123", reply: "Fortinet" }),
    );
    await waitFor(() =>
      expect(screen.queryByText("製品名を教えてください")).not.toBeInTheDocument(),
    );
  });

  it("shows an error on the followup form when postAnswer fails", async () => {
    postAnswerMock.mockRejectedValueOnce(new Error("network"));
    renderScreen(state({ followup: { question: "詳細を教えてください", missing: [] } }));

    fireEvent.change(screen.getByLabelText("補足の回答"), { target: { value: "詳細です" } });
    fireEvent.click(screen.getByRole("button", { name: "回答する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("回答の送信に失敗しました");
  });

  it("shows a terminal message (off-topic / no candidate)", () => {
    renderScreen(
      state({ terminal: true, message: { status: "off_topic", message: "業務外の質問です" } }),
    );
    expect(screen.getByText("業務外の質問です")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "新しい質問をする" })).toBeInTheDocument();
    expect(screen.queryByTestId("active-step")).not.toBeInTheDocument();
  });

  it("offers a link to the cited document on the document route (#143)", () => {
    renderScreen(
      state({
        terminal: true,
        message: {
          status: "document",
          message: "社内文書に該当がありそうです。",
          doc_id: "doc_001",
        },
      }),
    );
    const link = screen.getByRole("link", { name: /文書を見る/ });
    // Carries the session id so the viewer can send the reader back here (#179).
    expect(link).toHaveAttribute("href", "/documents/doc_001?from=abc-123");
  });

  it("offers the ranked person and continues the same document session (#351)", async () => {
    renderScreen(
      state({
        terminal: true,
        message: {
          status: "document",
          message: "社内文書に該当がありそうです。",
          doc_id: "doc_001",
          fallback_responder: {
            person_id: "E001",
            name: "中島 健一",
            score: 0.9,
            confidence: "高",
            reasons: [],
          },
        },
      }),
    );

    expect(screen.getByRole("heading")).toHaveTextContent("関連する社内文書が見つかりました");
    fireEvent.click(screen.getByRole("button", { name: "中島 健一さんに聞く" }));
    await waitFor(() => {
      expect(requestDocumentFallbackMock).toHaveBeenCalledWith({ session_id: "abc-123" });
      expect(pushMock).toHaveBeenCalledWith("/session/abc-123/result");
    });
  });

  it("does not offer a person action when the document has no fallback candidate", () => {
    renderScreen(
      state({
        terminal: true,
        message: { status: "document", message: "社内文書に該当", doc_id: "doc_001" },
      }),
    );
    expect(screen.queryByRole("button", { name: /さんに聞く/ })).not.toBeInTheDocument();
  });

  it("keeps the document result usable when starting the fallback fails", async () => {
    requestDocumentFallbackMock.mockRejectedValueOnce(new Error("network"));
    renderScreen(
      state({
        terminal: true,
        message: {
          status: "document",
          message: "社内文書に該当",
          fallback_responder: {
            person_id: "E001",
            name: "中島 健一",
            score: 0.9,
            confidence: "高",
            reasons: [],
          },
        },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "中島 健一さんに聞く" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "候補者への取り次ぎを開始できませんでした",
    );
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "中島 健一さんに聞く" })).toBeEnabled();
  });

  it("shows no document link when a terminal message carries no doc_id", () => {
    renderScreen(
      state({ terminal: true, message: { status: "no_candidate", message: "見つかりません" } }),
    );
    expect(screen.queryByRole("link", { name: /文書を見る/ })).not.toBeInTheDocument();
  });

  it("renders source citations of a self-answer as links/chips (#291/#293)", () => {
    renderScreen(
      state({
        terminal: true,
        message: {
          status: "self_answered",
          message: "既存の社内データから回答します。",
          citations: [
            { source_id: "doc_007", kind: "document" },
            { source_id: "qa_0042", kind: "qa" },
          ],
        },
      }),
    );
    // document citation is a link to the document viewer, carrying the session id.
    const docLink = screen.getByRole("link", { name: /doc_007/ });
    expect(docLink).toHaveAttribute("href", "/documents/doc_007?from=abc-123");
    // qa citation is a link to the knowledge detail viewer (#293 part2, /knowledge/[id]).
    const qaLink = screen.getByRole("link", { name: /qa_0042/ });
    expect(qaLink).toHaveAttribute("href", "/knowledge/qa_0042");
  });

  it("shows no 出典 block when a terminal message carries no citations", () => {
    renderScreen(
      state({ terminal: true, message: { status: "no_candidate", message: "見つかりません" } }),
    );
    expect(screen.queryByText("出典")).not.toBeInTheDocument();
  });

  it("shows a generic error display without leaking detail", () => {
    renderScreen(state({ error: "処理中にエラーが発生しました。" }));
    expect(screen.getByRole("alert")).toHaveTextContent("エラーが発生しました");
    expect(screen.queryByTestId("active-step")).not.toBeInTheDocument();
  });

  it("suppresses the recommend step and result CTA for an empty recommendation set", () => {
    renderScreen(state({ recommend: { recommendations: [] } }));
    expect(screen.queryByText(/候補を/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "結果を見る" })).not.toBeInTheDocument();
  });

  it("renders the draft step when a draft has content", () => {
    renderScreen(state({ draft: { draft: "以下の依頼文でいかがでしょうか。" } }));
    expect(screen.getByText("依頼文を作成しました")).toBeInTheDocument();
    expect(screen.getByText("以下の依頼文でいかがでしょうか。")).toBeInTheDocument();
  });

  it("suppresses the draft step when the draft is empty", () => {
    renderScreen(state({ draft: { draft: "" } }));
    expect(screen.queryByText("依頼文を作成しました")).not.toBeInTheDocument();
  });

  it("grants result access from a draft alone (reconnect at the send interrupt)", () => {
    // A refresh at `send` replays only the draft (no recommend) — still resultable.
    renderScreen(state({ draft: { draft: "高梨さんへの依頼文" } }));
    expect(screen.getByRole("button", { name: "結果を見る" })).toBeInTheDocument();
  });

  it("hides the followup form once the stream advances past it (ack-loss recovery)", () => {
    const events: StreamEvent[] = [
      { event: "followup", data: { question: "製品名を教えてください", missing: [] } },
      { event: "route", data: { route: "person", reason: "詳しい人がいます", confidence: 0.7 } },
    ];
    renderScreen(
      state({
        events,
        followup: { question: "製品名を教えてください", missing: [] },
        route: { route: "person", reason: "詳しい人がいます", confidence: 0.7 },
      }),
    );
    expect(screen.queryByLabelText("補足の回答")).not.toBeInTheDocument();
    expect(screen.getByText("回答の経路を判断しました")).toBeInTheDocument();
  });

  it("treats a 409 from postAnswer as success and closes the form", async () => {
    postAnswerMock.mockRejectedValueOnce(new ApiError(409, "conflict"));
    renderScreen(state({ followup: { question: "詳細を教えてください", missing: [] } }));

    fireEvent.change(screen.getByLabelText("補足の回答"), { target: { value: "詳細です" } });
    fireEvent.click(screen.getByRole("button", { name: "回答する" }));

    await waitFor(() => expect(postAnswerMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText("詳細を教えてください")).not.toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stops showing the in-progress step once the run is done", () => {
    renderScreen(state({ terminal: true, done: { status: "sent" } }));
    expect(screen.queryByTestId("active-step")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "結果を見る" })).toBeInTheDocument();
  });

  it("reads the stream from SessionStreamProvider context (no own subscription)", () => {
    const openedFactories: string[] = [];
    const factory = (url: string) => {
      openedFactories.push(url);
      return {
        addEventListener: () => {},
        removeEventListener: () => {},
        close: () => {},
        onerror: null,
        readyState: 1,
      } as unknown as EventSource;
    };
    render(
      <SessionStreamProvider
        sessionId="abc-123"
        streamState={state({
          recommend: {
            recommendations: [
              { person_id: "E001", name: "高梨", score: 0.9, confidence: "high", reasons: [] },
            ],
          },
        })}
      >
        <ProcessingScreen sessionId="abc-123" eventSourceFactory={factory} />
      </SessionStreamProvider>,
    );
    // Reads context: shows the recommend step, and its own subscription stays off.
    expect(screen.getByText("候補を1名見つけました")).toBeInTheDocument();
    expect(openedFactories).toEqual([]);
  });

  it("opens a single EventSource via the live junction when no streamState is injected", () => {
    const urls: string[] = [];
    const factory = (url: string) => {
      urls.push(url);
      return {
        addEventListener: () => {},
        removeEventListener: () => {},
        close: () => {},
        onerror: null,
        readyState: 1,
      } as unknown as EventSource;
    };
    render(
      <ProcessingScreen
        sessionId="abc-123"
        baseUrl="http://api.test"
        eventSourceFactory={factory}
      />,
    );
    expect(urls).toEqual(["http://api.test/events/abc-123"]);
    expect(screen.getByText("最適な回答者を探しています…")).toBeInTheDocument();
  });

  describe("similar-asker reassurance (#475 Screen 01)", () => {
    function understood(count: number) {
      return state({
        understood: {
          topics: ["ネットワーク"],
          products: [],
          situation: "",
          question_type: "how",
          confidence: 0.8,
          similar_asker_count: count,
        },
      });
    }

    it("shows how many other people asked in this area when the count is ≥ 1", () => {
      renderScreen(understood(3));
      expect(
        screen.getByText(/同じ分野で、過去に3人が質問しています。あなただけではありません。/),
      ).toBeInTheDocument();
    });

    it("hides the reassurance when the count is 0", () => {
      renderScreen(understood(0));
      expect(screen.queryByText(/あなただけではありません/)).not.toBeInTheDocument();
    });

    it("hides the reassurance when the field is absent (feature off / old payload)", () => {
      renderScreen(
        state({
          understood: {
            topics: ["ネットワーク"],
            products: [],
            situation: "",
            question_type: "how",
            confidence: 0.8,
          },
        }),
      );
      expect(screen.queryByText(/あなただけではありません/)).not.toBeInTheDocument();
    });
  });

  describe("staggered step reveal (#475 Screen 01)", () => {
    it("reveals each thinking step with the reveal animation, disabled under reduced motion", () => {
      renderScreen(
        state({
          understood: {
            topics: ["ネットワーク"],
            products: [],
            situation: "",
            question_type: "how",
            confidence: 0.8,
          },
        }),
      );
      // The step content is still present (reveal is opacity/transform only).
      const step = screen.getByText("質問を理解しました").closest("li");
      expect(step).not.toBeNull();
      expect(step).toHaveClass("animate-reveal");
      expect(step).toHaveClass("motion-reduce:animate-none");
      expect(step).toHaveStyle({ animationDelay: "0ms" });
    });

    it("staggers a later step so it slides in after the earlier one", () => {
      renderScreen(
        state({
          understood: {
            topics: ["ネットワーク"],
            products: [],
            situation: "",
            question_type: "how",
            confidence: 0.8,
          },
          route: { route: "person", reason: "詳しい人がいます", confidence: 0.7 },
        }),
      );
      const routeStep = screen.getByText("回答の経路を判断しました").closest("li");
      expect(routeStep).toHaveStyle({ animationDelay: "70ms" });
    });
  });
});
