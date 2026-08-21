import { ResultScreen } from "@/components/ResultScreen";
import { SessionStreamProvider } from "@/components/SessionStreamProvider";
import type { Recommendation } from "@/lib/api-types";
import type { EventStreamState } from "@/hooks/useEventStream";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

function rec(
  partial: Partial<Recommendation> & { person_id: string; name: string },
): Recommendation {
  return { dept: null, score: 0.5, confidence: "mid", reasons: [], ...partial };
}

function state(partial: Partial<EventStreamState>): EventStreamState {
  return { events: [], terminal: false, ...partial };
}

function renderResult(stream: EventStreamState) {
  return render(<ResultScreen streamState={stream} />);
}

const THREE_CANDIDATES: Recommendation[] = [
  {
    person_id: "E001",
    name: "高梨",
    dept: "営業本部",
    score: 0.92,
    confidence: "high",
    reasons: [
      { type: "cert", detail: "関連資格保持" },
      { type: "answers", detail: "過去回答: 45件" },
      { type: "load", detail: "低" },
    ],
  },
  { person_id: "E002", name: "鈴木", dept: "技術部", score: 0.85, confidence: "mid", reasons: [] },
  { person_id: "E003", name: "田中", dept: "法務部", score: 0.78, confidence: "mid", reasons: [] },
];

describe("ResultScreen — pending", () => {
  it("shows a preparing placeholder when no data has arrived", () => {
    renderResult(state({}));
    expect(screen.getByText("結果を準備中…")).toBeInTheDocument();
  });
});

describe("ResultScreen — main line (person)", () => {
  it("renders up to three candidates with fit score and reason labels", () => {
    renderResult(
      state({
        route: { route: "person", reason: "同様の案件担当者がいます", confidence: 0.9 },
        recommend: {
          recommendations: [
            ...THREE_CANDIDATES,
            { person_id: "E004", name: "山田", score: 0.5, confidence: "low", reasons: [] },
          ],
        },
        draft: { draft: "お疲れ様です。" },
      }),
    );

    expect(screen.getByText("この質問は、人に聞くのが確実です")).toBeInTheDocument();
    expect(screen.getByText("同様の案件担当者がいます")).toBeInTheDocument();
    expect(screen.getByText("高梨（最有力）")).toBeInTheDocument();
    expect(screen.getByText("鈴木")).toBeInTheDocument();
    expect(screen.getByText("田中")).toBeInTheDocument();
    // 4th candidate is truncated (max 3)
    expect(screen.queryByText("山田")).not.toBeInTheDocument();
    // fit score as percent
    expect(screen.getByText("適合度 92%")).toBeInTheDocument();
    // reason labels (expanded top card shows detail)
    expect(screen.getByText("関連資格")).toBeInTheDocument();
    expect(screen.getByText("過去回答")).toBeInTheDocument();
  });

  it("lets the user edit the draft (change reflected in the textarea)", () => {
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "元の下書き" },
      }),
    );
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("聞き方の下書き");
    expect(textarea.value).toBe("元の下書き");
    fireEvent.change(textarea, { target: { value: "編集後の本文" } });
    expect(textarea.value).toBe("編集後の本文");
  });

  it("confirms the send to the selected candidate", () => {
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "本文" },
      }),
    );
    // select the 2nd candidate, then send
    fireEvent.click(screen.getAllByRole("button", { name: "選択する" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "この方に送る" }));
    expect(screen.getByText("送信しました")).toBeInTheDocument();
    expect(screen.getByText(/鈴木さんに依頼を送りました/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "新しい質問をする" })).toBeInTheDocument();
  });

  it("defaults to the main line when the route is unset but candidates exist", () => {
    renderResult(
      state({ recommend: { recommendations: THREE_CANDIDATES }, draft: { draft: "x" } }),
    );
    expect(screen.getByText("この質問は、人に聞くのが確実です")).toBeInTheDocument();
  });

  it("shows a candidate-preparing note and disables send when only a draft exists", () => {
    renderResult(
      state({ route: { route: "person", reason: "", confidence: 0.9 }, draft: { draft: "本文" } }),
    );
    expect(screen.getByText("候補を確認しています…")).toBeInTheDocument();
    // reason fallback text
    expect(screen.getByText(/直近で同様の案件を担当した方/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "この方に送る" })).toBeDisabled();
  });

  it("renders a candidate with no department and no reason detail", () => {
    renderResult(
      state({
        route: { route: "person", reason: "r", confidence: 0.9 },
        recommend: {
          recommendations: [
            rec({
              person_id: "E020",
              name: "佐藤",
              dept: null,
              score: 0.6,
              reasons: [{ type: "self", detail: "" }],
            }),
          ],
        },
        draft: { draft: "x" },
      }),
    );
    expect(screen.getByText("佐藤（最有力）")).toBeInTheDocument();
    expect(screen.getByText("E020")).toBeInTheDocument();
    expect(screen.getByText("得意分野")).toBeInTheDocument();
  });
});

describe("ResultScreen — auxiliary (prior_answer)", () => {
  const auxState = state({
    route: { route: "prior_answer", reason: "2023/10/12に同様の質問に回答", confidence: 0.8 },
    recommend: {
      recommendations: [
        {
          person_id: "E010",
          name: "高梨",
          dept: "法務部",
          score: 0.9,
          confidence: "high",
          reasons: [{ type: "answers", detail: "過去回答: 30件" }],
        },
      ],
    },
  });

  it("shows the person as evidence, with reuse count and both actions", () => {
    renderResult(auxState);
    expect(screen.getByText("この質問には、高梨さんが詳しそうです")).toBeInTheDocument();
    expect(screen.getByText(/2023\/10\/12に同様の質問に回答/)).toBeInTheDocument();
    expect(screen.getByText(/30 人に役立ちました/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "解決した" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "この方に追加で聞く" })).toBeInTheDocument();
  });

  it("drops to the main line when 追加で聞く is pressed", () => {
    renderResult(auxState);
    fireEvent.click(screen.getByRole("button", { name: "この方に追加で聞く" }));
    expect(screen.getByText("この質問は、人に聞くのが確実です")).toBeInTheDocument();
  });

  it("shows a completion state when 解決した is pressed", () => {
    renderResult(auxState);
    fireEvent.click(screen.getByRole("button", { name: "解決した" }));
    expect(screen.getByText("解決しました")).toBeInTheDocument();
  });

  it("falls back to placeholders when no answerer/evidence is available", () => {
    renderResult(state({ route: { route: "prior_answer", reason: "", confidence: 0.8 } }));
    expect(screen.getByText("この質問には、詳しい方さんが詳しそうです")).toBeInTheDocument();
    expect(screen.getByText(/同様の質問に過去に回答しています/)).toBeInTheDocument();
    expect(screen.getByText("過去の回答内容を確認しています。")).toBeInTheDocument();
    expect(screen.queryByText(/役立ちました/)).not.toBeInTheDocument();
  });
});

describe("ResultScreen — via provider context", () => {
  it("reads the stream from SessionStreamProvider", () => {
    render(
      <SessionStreamProvider
        sessionId="abc-123"
        streamState={state({
          route: { route: "person", reason: "", confidence: 0.9 },
          recommend: { recommendations: THREE_CANDIDATES },
          draft: { draft: "本文" },
        })}
      >
        <ResultScreen />
      </SessionStreamProvider>,
    );
    expect(screen.getByText("この質問は、人に聞くのが確実です")).toBeInTheDocument();
  });
});
