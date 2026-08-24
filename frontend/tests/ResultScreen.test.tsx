import { ResultScreen } from "@/components/ResultScreen";
import { SessionStreamProvider } from "@/components/SessionStreamProvider";
import type { Recommendation } from "@/lib/api-types";
import type { EventStreamState } from "@/hooks/useEventStream";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const updateHandoffDraftMock = vi.fn();
const selectHandoffCandidateMock = vi.fn();
const excludeHandoffCandidateMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  updateHandoffDraft: (...args: unknown[]) => updateHandoffDraftMock(...args),
  selectHandoffCandidate: (...args: unknown[]) => selectHandoffCandidateMock(...args),
  excludeHandoffCandidate: (...args: unknown[]) => excludeHandoffCandidateMock(...args),
}));

beforeEach(() => {
  updateHandoffDraftMock.mockReset();
  updateHandoffDraftMock.mockResolvedValue({ session_id: "s1", status: "draft_saved" });
  selectHandoffCandidateMock.mockReset();
  excludeHandoffCandidateMock.mockReset();
  excludeHandoffCandidateMock.mockResolvedValue({ session_id: "s1", status: "reroute_queued" });
});

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

function renderResult(stream: EventStreamState, sessionId = "s1") {
  return render(<ResultScreen streamState={stream} sessionId={sessionId} />);
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
    // fit signal is the score-derived 適合度% in the gauge, with the confidence
    // label as a separate badge (#240); 高梨's score 0.92 normalises to 100%. The
    // visible ring number carries no "%" sign (the % lives only in the aria-label).
    expect(screen.getByRole("img", { name: "適合度 100%・確信度 高" })).toBeInTheDocument();
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

  it("clears a stale send confirmation when a reroute changes the recipient", async () => {
    const { rerender } = render(
      <ResultScreen
        sessionId="s1"
        streamState={state({
          route: { route: "person", reason: "", confidence: 0.9 },
          recommend: { recommendations: [rec({ person_id: "E001", name: "高梨" })] },
          draft: { draft: "高梨さん向け" },
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "この内容で依頼する" }));
    expect(await screen.findByText("依頼を送りました")).toBeInTheDocument();

    // The recipient declines → reroute to a new person. The stale success
    // confirmation must not persist and block the new candidate's route view.
    rerender(
      <ResultScreen
        sessionId="s1"
        streamState={state({
          route: { route: "person", reason: "", confidence: 0.9 },
          recommend: { recommendations: [rec({ person_id: "E002", name: "鈴木" })] },
          draft: { draft: "鈴木さん向け" },
        })}
      />,
    );
    expect(screen.queryByText("依頼を送りました")).not.toBeInTheDocument();
    expect(screen.getByText("鈴木（最有力）")).toBeInTheDocument();
  });

  it("confirms the send: POSTs the edited draft to the pending hand-off (#174)", async () => {
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "元の下書き" },
      }),
    );
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("聞き方の下書き");
    fireEvent.change(textarea, { target: { value: "編集後の依頼文" } });
    fireEvent.click(screen.getByRole("button", { name: "この内容で依頼する" }));

    // The edited text is persisted for this session, and the top pick is the recipient.
    await waitFor(() =>
      expect(updateHandoffDraftMock).toHaveBeenCalledWith({
        session_id: "s1",
        draft: "編集後の依頼文",
      }),
    );
    expect(await screen.findByText("依頼を送りました")).toBeInTheDocument();
    expect(screen.getByText(/高梨さんに、この内容でお繋ぎしました/)).toBeInTheDocument();
  });

  it("shows full reason detail on all three candidates, not just the top pick (#204/#A2)", () => {
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: {
          recommendations: [
            THREE_CANDIDATES[0],
            {
              ...THREE_CANDIDATES[1],
              reasons: [{ type: "proximity", detail: "同じ拠点（大阪）" }],
            },
            {
              ...THREE_CANDIDATES[2],
              reasons: [{ type: "load", detail: "今週の対応件数: 普通" }],
            },
          ],
        },
        draft: { draft: "本文" },
      }),
    );
    // Rank 2/3 detail text used to be suppressed (only the label chip showed);
    // now every shown candidate renders its full reason detail.
    expect(screen.getByText(/同じ拠点（大阪）/)).toBeInTheDocument();
    expect(screen.getByText(/今週の対応件数: 普通/)).toBeInTheDocument();
  });

  it("lets the asker pick any of the three candidates as the recipient (#200/#A1/#204/#C4)", async () => {
    selectHandoffCandidateMock.mockResolvedValue({
      session_id: "s1",
      responder: THREE_CANDIDATES[1],
      draft: "鈴木さん向けに調整した下書き",
      recommendation_id: 42,
    });
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "高梨さん向けの下書き" },
      }),
    );
    // The top pick starts selected; every card offers a selection control now.
    expect(screen.getByRole("button", { name: "選択中" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "選択する" })).toHaveLength(2);

    fireEvent.click(screen.getAllByRole("button", { name: "選択する" })[0]); // 鈴木 (E002)
    await waitFor(() =>
      expect(selectHandoffCandidateMock).toHaveBeenCalledWith({
        session_id: "s1",
        person_id: "E002",
      }),
    );
    // The regenerated draft (for the newly selected candidate) replaces the editor's text.
    expect(await screen.findByLabelText<HTMLTextAreaElement>("聞き方の下書き")).toHaveValue(
      "鈴木さん向けに調整した下書き",
    );

    // The send confirmation now names the selected candidate, not the original top pick.
    fireEvent.click(screen.getByRole("button", { name: "この内容で依頼する" }));
    expect(await screen.findByText(/鈴木さんに、この内容でお繋ぎしました/)).toBeInTheDocument();
  });

  it("surfaces a retryable error when reselecting a candidate fails", async () => {
    selectHandoffCandidateMock.mockRejectedValueOnce(new Error("boom"));
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "本文" },
      }),
    );
    fireEvent.click(screen.getAllByRole("button", { name: "選択する" })[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("候補の切り替えに失敗しました");
    // Selection did not change on failure: still one "選択中" and two "選択する".
    expect(screen.getByRole("button", { name: "選択中" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "選択する" })).toHaveLength(2);
  });

  it("excludes the send target via 'この人には聞かない' and shows the reroute status (#260)", async () => {
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "高梨さん向けの下書き" },
      }),
    );
    // The control is offered ONLY on the current send target (the selected top).
    expect(screen.getAllByRole("button", { name: "この人には聞かない" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "この人には聞かない" }));
    await waitFor(() =>
      expect(excludeHandoffCandidateMock).toHaveBeenCalledWith({
        session_id: "s1",
        person_id: THREE_CANDIDATES[0].person_id,
      }),
    );
    // The freshly-scored candidate arrives over the stream (remount); meanwhile the
    // asker sees a pending status and the send button is disabled.
    expect(await screen.findByRole("status")).toHaveTextContent("別の方を選び直しています");
    expect(screen.getByRole("button", { name: "この内容で依頼する" })).toBeDisabled();
  });

  it("surfaces a retryable error when excluding the send target fails (#260)", async () => {
    excludeHandoffCandidateMock.mockRejectedValueOnce(new Error("boom"));
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "本文" },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "この人には聞かない" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("候補の選び直しに失敗しました");
    // The send button is re-enabled on failure so the asker can retry / send anyway.
    expect(screen.getByRole("button", { name: "この内容で依頼する" })).not.toBeDisabled();
  });

  it("surfaces a retryable error when the confirm POST fails", async () => {
    updateHandoffDraftMock.mockRejectedValueOnce(new Error("boom"));
    renderResult(
      state({
        route: { route: "person", reason: "", confidence: 0.9 },
        recommend: { recommendations: THREE_CANDIDATES },
        draft: { draft: "本文" },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "この内容で依頼する" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("送信に失敗しました");
    // Still on the route view (not the sent-confirmation), so the asker can retry.
    expect(screen.queryByText("依頼を送りました")).not.toBeInTheDocument();
  });

  it("drops the send confirmation if a reroute remounts the view mid-POST (#174 review)", async () => {
    // The POST is in flight when the current responder declines and the reroute
    // remounts PersonRouteView (new key). The resolved POST must not paint a stale
    // "sent" confirmation onto the new candidate's view.
    let resolvePost: (v: unknown) => void = () => {};
    updateHandoffDraftMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePost = resolve;
      }),
    );
    const first = state({
      route: { route: "person", reason: "", confidence: 0.9 },
      recommend: { recommendations: [rec({ person_id: "E001", name: "高梨" })] },
      draft: { draft: "高梨さん向け" },
    });
    const { rerender } = render(<ResultScreen sessionId="s1" streamState={first} />);
    fireEvent.click(screen.getByRole("button", { name: "この内容で依頼する" }));

    // Reroute to a new top candidate BEFORE the POST resolves -> remount.
    rerender(
      <ResultScreen
        sessionId="s1"
        streamState={state({
          route: { route: "person", reason: "", confidence: 0.9 },
          recommend: { recommendations: [rec({ person_id: "E002", name: "鈴木" })] },
          draft: { draft: "鈴木さん向け" },
        })}
      />,
    );
    resolvePost({ session_id: "s1", status: "draft_saved" });
    await Promise.resolve();

    expect(screen.queryByText("依頼を送りました")).not.toBeInTheDocument();
    expect(screen.getByText("鈴木（最有力）")).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "この内容で依頼する" })).toBeEnabled();
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
