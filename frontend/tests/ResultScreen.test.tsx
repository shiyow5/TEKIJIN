import { ResultScreen } from "@/components/ResultScreen";
import { SessionStreamProvider } from "@/components/SessionStreamProvider";
import type { Recommendation } from "@/lib/api-types";
import type { EventStreamState } from "@/hooks/useEventStream";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

// Fixtures use the REAL backend shapes: `confidence` is the Japanese fit signal
// 高/中/低 (scorer.confidence_label), and `reasons[].detail` carries the verbatim
// evidence strings the scorer emits. `score` is a raw weighted value, never shown.
function rec(
  partial: Partial<Recommendation> & { person_id: string; name: string },
): Recommendation {
  return { dept: null, score: 0.5, confidence: "中", reasons: [], ...partial };
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
    confidence: "高",
    reasons: [
      { type: "cert", detail: "第一種電気工事士" },
      { type: "answers", detail: "類似の質問に過去45件回答（うち有用と評価30件）" },
      { type: "load", detail: "今週の対応件数: 少なめ" },
    ],
  },
  { person_id: "E002", name: "鈴木", dept: "技術部", score: 0.85, confidence: "中", reasons: [] },
  { person_id: "E003", name: "田中", dept: "法務部", score: 0.78, confidence: "中", reasons: [] },
];

describe("ResultScreen — pending", () => {
  it("shows a preparing placeholder when no data has arrived", () => {
    renderResult(state({}));
    expect(screen.getByText("結果を準備中…")).toBeInTheDocument();
  });
});

describe("ResultScreen — terminal-only replay (hard reload)", () => {
  it("shows a sent-completion state when only a done event was replayed", () => {
    // A finished, sent session hard-reloaded replays only `done` — no route /
    // recommend / draft to hydrate. It must not stall on 「結果を準備中」.
    renderResult(state({ terminal: true, done: { status: "sent" } }));
    expect(screen.getByText("依頼は送信済みです")).toBeInTheDocument();
    expect(screen.queryByText("結果を準備中…")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "新しい質問をする" })).toBeInTheDocument();
  });

  it("shows a terminal message (off-topic / no candidate) replayed on reload", () => {
    renderResult(
      state({
        terminal: true,
        message: { status: "no_candidate", message: "該当者が見つかりませんでした" },
      }),
    );
    expect(screen.getByText("該当者が見つかりませんでした")).toBeInTheDocument();
    // The heading must reflect the no-candidate outcome, not promise a delivery.
    expect(
      screen.getByRole("heading", { level: 1, name: "担当者が見つかりませんでした" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("結果を準備中…")).not.toBeInTheDocument();
  });

  it("heads an unresolved terminal with a could-not-identify title", () => {
    renderResult(
      state({
        terminal: true,
        message: { status: "unresolved", message: "ご質問の内容を特定できませんでした" },
      }),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "ご質問を特定できませんでした" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("回答をお届けします")).not.toBeInTheDocument();
  });

  it("prioritises a live terminal outcome over a retained route (all declined)", () => {
    // After every responder declines, the empty recommend clears the draft and
    // the backend emits a no_candidate message while `route` is still populated.
    // The terminal outcome must win over the stale route, not fall through to 準備中.
    renderResult(
      state({
        route: { route: "person", reason: "詳しい人がいます", confidence: 0.7 },
        recommend: { recommendations: [] },
        terminal: true,
        message: { status: "no_candidate", message: "対応できる担当者が見つかりませんでした" },
      }),
    );
    expect(screen.getByText("対応できる担当者が見つかりませんでした")).toBeInTheDocument();
    expect(screen.queryByText("結果を準備中…")).not.toBeInTheDocument();
  });

  it("heads an off_topic terminal with an out-of-scope title, not a delivery promise", () => {
    renderResult(
      state({ terminal: true, message: { status: "off_topic", message: "業務外のご質問です" } }),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "対象外のご質問です" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("回答をお届けします")).not.toBeInTheDocument();
  });

  it("heads a document terminal with a delivery title (it self-resolves)", () => {
    renderResult(
      state({
        terminal: true,
        message: { status: "document", message: "社内文書に該当があります", doc_id: "d1" },
      }),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "回答をお届けします" }),
    ).toBeInTheDocument();
  });
});

describe("ResultScreen — stream error", () => {
  it("surfaces a generic error (no leaked detail) instead of stalling on 準備中", () => {
    renderResult(state({ error: "処理中にエラーが発生しました。" }));
    expect(
      screen.getByRole("heading", { level: 1, name: "エラーが発生しました" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("時間をおいて再度お試しください");
    expect(screen.queryByText("結果を準備中…")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "新しい質問をする" })).toBeInTheDocument();
  });
});

describe("ResultScreen — main line (person)", () => {
  it("renders up to three candidates with the fit signal and reason labels", () => {
    renderResult(
      state({
        route: { route: "person", reason: "同様の案件担当者がいます", confidence: 0.9 },
        recommend: {
          recommendations: [
            ...THREE_CANDIDATES,
            { person_id: "E004", name: "山田", score: 0.5, confidence: "低", reasons: [] },
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
    // fit signal is the Japanese confidence label (now an animated gauge, #139),
    // never a raw score percentage.
    expect(screen.getByRole("img", { name: "適合度 高" })).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
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

  it("resets the draft editor when a reroute changes the recipient (discards stale edit)", () => {
    const { rerender } = render(
      <ResultScreen
        streamState={state({
          route: { route: "person", reason: "", confidence: 0.9 },
          recommend: { recommendations: [rec({ person_id: "E001", name: "高梨" })] },
          draft: { draft: "高梨さん向けの下書き" },
        })}
      />,
    );
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("聞き方の下書き");
    fireEvent.change(textarea, { target: { value: "高梨さん宛に編集した本文" } });
    expect(textarea.value).toBe("高梨さん宛に編集した本文");

    // A decline reroutes to a different person with a fresh draft. The editor
    // must remount and show the new recipient's draft, not the stale edit.
    rerender(
      <ResultScreen
        streamState={state({
          route: { route: "person", reason: "", confidence: 0.9 },
          recommend: { recommendations: [rec({ person_id: "E002", name: "鈴木" })] },
          draft: { draft: "鈴木さん向けの下書き" },
        })}
      />,
    );
    expect(screen.getByLabelText<HTMLTextAreaElement>("聞き方の下書き").value).toBe(
      "鈴木さん向けの下書き",
    );
  });

  it("clears a stale send confirmation when a reroute changes the recipient", () => {
    const { rerender } = render(
      <ResultScreen
        streamState={state({
          route: { route: "person", reason: "", confidence: 0.9 },
          recommend: { recommendations: [rec({ person_id: "E001", name: "高梨" })] },
          draft: { draft: "高梨さん向け" },
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "この方に送る" }));
    expect(screen.getByText("送信しました")).toBeInTheDocument();

    // The recipient declines → reroute to a new person. The stale success
    // confirmation must not persist and block the new candidate's route view.
    rerender(
      <ResultScreen
        streamState={state({
          route: { route: "person", reason: "", confidence: 0.9 },
          recommend: { recommendations: [rec({ person_id: "E002", name: "鈴木" })] },
          draft: { draft: "鈴木さん向け" },
        })}
      />,
    );
    expect(screen.queryByText("送信しました")).not.toBeInTheDocument();
    expect(screen.getByText("鈴木（最有力）")).toBeInTheDocument();
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

  it("warns before sending when a non-top candidate is selected (draft mismatch)", () => {
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "本文" },
      }),
    );
    // No warning while the top candidate is selected.
    expect(screen.queryByText(/宛先を変える場合は本文を編集/)).not.toBeInTheDocument();
    // Selecting the 2nd candidate surfaces the mismatch warning.
    fireEvent.click(screen.getAllByRole("button", { name: "選択する" })[0]);
    expect(screen.getByText(/下書きは最有力の高梨/)).toBeInTheDocument();
  });

  it("defaults to the main line when the route is unset but candidates exist", () => {
    renderResult(
      state({ recommend: { recommendations: THREE_CANDIDATES }, draft: { draft: "x" } }),
    );
    expect(screen.getByText("この質問は、人に聞くのが確実です")).toBeInTheDocument();
  });

  it("keeps the draft sendable with a fallback note when only a draft exists (reconnect at send interrupt)", () => {
    renderResult(
      state({ route: { route: "person", reason: "", confidence: 0.9 }, draft: { draft: "本文" } }),
    );
    // Graceful fallback: candidates missing, but the draft is still sendable.
    expect(screen.getByText(/宛先候補を再取得しています/)).toBeInTheDocument();
    // reason fallback text
    expect(screen.getByText(/直近で同様の案件を担当した方/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "この方に送る" })).toBeEnabled();
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
    // The self-declared-skill reason label renders even without a detail string.
    expect(screen.getByText("自己申告")).toBeInTheDocument();
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
          confidence: "高",
          reasons: [{ type: "answers", detail: "類似の質問に過去30件回答（うち有用と評価20件）" }],
        },
      ],
    },
  });

  it("shows the person as evidence, with the past-answer record and both actions", () => {
    renderResult(auxState);
    expect(screen.getByText("この質問には、高梨さんが詳しそうです")).toBeInTheDocument();
    expect(screen.getByText(/2023\/10\/12に同様の質問に回答/)).toBeInTheDocument();
    // The record is the verbatim `answers` evidence — not a fabricated reuse count.
    expect(screen.getByText("類似の質問に過去30件回答（うち有用と評価20件）")).toBeInTheDocument();
    expect(screen.queryByText(/役立ちました/)).not.toBeInTheDocument();
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

  it("falls back to placeholders and disables 追加で聞く when there is no main line", () => {
    renderResult(state({ route: { route: "prior_answer", reason: "", confidence: 0.8 } }));
    expect(screen.getByText("この質問には、詳しい方さんが詳しそうです")).toBeInTheDocument();
    expect(screen.getByText(/同様の質問に過去に回答しています/)).toBeInTheDocument();
    expect(screen.getByText("過去の回答実績を確認しています。")).toBeInTheDocument();
    expect(screen.queryByText(/役立ちました/)).not.toBeInTheDocument();
    // No candidates or draft to drop to → the ask-more action is disabled (no dead-end).
    expect(screen.getByRole("button", { name: "この方に追加で聞く" })).toBeDisabled();
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
