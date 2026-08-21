import { ProcessingScreen } from "@/components/ProcessingScreen";
import { ApiError } from "@/lib/api-client";
import type { EventStreamState, StreamEvent } from "@/hooks/useEventStream";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const postAnswerMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  postAnswer: (...args: unknown[]) => postAnswerMock(...args),
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
  });

  afterEach(() => vi.restoreAllMocks());

  it("shows an in-progress step while no events have arrived yet", () => {
    renderScreen(state({}));
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
    expect(screen.getByText("確信度 85%")).toBeInTheDocument();
  });

  it("renders the route step and confidence", () => {
    renderScreen(
      state({ route: { route: "person", reason: "詳しい人がいます", confidence: 0.7 } }),
    );
    expect(screen.getByText("回答の経路を判断しました")).toBeInTheDocument();
    expect(screen.getByText("確信度 70%")).toBeInTheDocument();
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
});
