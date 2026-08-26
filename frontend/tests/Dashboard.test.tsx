import { Dashboard } from "@/components/Dashboard";
import type { DashboardResponse } from "@/lib/api-types";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getDashboardMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getDashboard: (...args: unknown[]) => getDashboardMock(...args),
  ApiError: class ApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

const DATA: DashboardResponse = {
  total_employees: 40,
  total_questions: 150,
  total_answers: 150,
  recommendation_count: 12,
  recommendation_outcomes: { accepted: 6, declined: 2, pending: 4 },
  acceptance_rate: 0.75,
  self_resolution_rate: 0.42,
  avg_resolution_hours: 3.5,
  top_responder_share: 0.18,
  processing_latency: { p50_ms: 820, p95_ms: 2900, sample_size: 42 },
  latest_eval: {
    top1_accuracy: 0.7,
    recall_at_3: 0.6,
    mrr: 0.72,
    route_accuracy: 0.7,
    created_at: "2026-08-22T00:00:00",
  },
  answers_per_responder: [
    { employee_id: 3, name: "高梨 健太", answer_count: 20 },
    { employee_id: 5, name: "藤田 悠斗", answer_count: 12 },
  ],
  topic_distribution: [
    { topic: "ネットワーク・VPN", count: 30 },
    { topic: "基幹システム", count: 18 },
  ],
  feedback_by_stage: { c1: 2, c6: 5, c7: 3, total: 10 },
  knowledge_accumulation: {
    this_month: 7,
    last_month: 4,
    captured_answers: 5,
    consult_retrospectives: 2,
    accepted_handoffs: 10,
    capture_rate: 0.5,
    monthly: [
      { month: "2026-04", count: 0 },
      { month: "2026-05", count: 1 },
      { month: "2026-06", count: 3 },
      { month: "2026-07", count: 2 },
      { month: "2026-08", count: 4 },
      { month: "2026-09", count: 7 },
    ],
  },
};

describe("Dashboard", () => {
  beforeEach(() => {
    getDashboardMock.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows this month's formalized knowledge with the change from last month (#294)", async () => {
    getDashboardMock.mockResolvedValue(DATA);
    render(<Dashboard />);

    const card = (await screen.findByText("今月の形式知化")).closest("div") as HTMLElement;
    expect(within(card).getByText("7")).toBeInTheDocument();
    // The delta is what makes a growing counter readable: 7 vs 4 last month.
    expect(within(card).getByText(/前月 4/)).toBeInTheDocument();
  });

  it("shows the recovery rate of hand-offs, not just the raw count (#294)", async () => {
    // Raw counts only ever grow; the capture rate is the one that can fall, which
    // is what makes it worth a card.
    getDashboardMock.mockResolvedValue(DATA);
    render(<Dashboard />);

    const card = (await screen.findByText("暗黙知の回収率")).closest("div") as HTMLElement;
    expect(within(card).getByText("50%")).toBeInTheDocument();
    expect(within(card).getByText(/10件/)).toBeInTheDocument();
  });

  it("breaks the month down by where the knowledge came from (#294)", async () => {
    getDashboardMock.mockResolvedValue(DATA);
    render(<Dashboard />);

    expect(await screen.findByText(/回答の蓄積/)).toBeInTheDocument();
    expect(screen.getByText(/直接相談のふりかえり/)).toBeInTheDocument();
  });

  it("renders the monthly trend oldest-first, including empty months (#294)", async () => {
    getDashboardMock.mockResolvedValue(DATA);
    render(<Dashboard />);

    const trend = await screen.findByRole("list", { name: "形式知化の月次推移" });
    const labels = within(trend)
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(labels).toHaveLength(6);
    expect(labels[0]).toContain("2026-04");
    expect(labels[5]).toContain("2026-09");
  });

  it("shows — rather than 0% when no hand-off was accepted this month (#294)", async () => {
    // "Nothing to measure" must not read as "we recovered nothing".
    getDashboardMock.mockResolvedValue({
      ...DATA,
      knowledge_accumulation: {
        ...DATA.knowledge_accumulation,
        accepted_handoffs: 0,
        capture_rate: 0,
      },
    });
    render(<Dashboard />);

    const card = (await screen.findByText("暗黙知の回収率")).closest("div") as HTMLElement;
    expect(within(card).getByText("—")).toBeInTheDocument();
    expect(within(card).queryByText("0%")).toBeNull();
  });

  it("keeps the trend visible in a month with no new knowledge (#294)", async () => {
    // A quiet month is not an empty state: the six-month trend is exactly what
    // makes a zero readable.
    getDashboardMock.mockResolvedValue({
      ...DATA,
      knowledge_accumulation: {
        ...DATA.knowledge_accumulation,
        this_month: 0,
        captured_answers: 0,
        consult_retrospectives: 0,
        monthly: [
          { month: "2026-04", count: 0 },
          { month: "2026-05", count: 2 },
          { month: "2026-06", count: 3 },
          { month: "2026-07", count: 1 },
          { month: "2026-08", count: 4 },
          { month: "2026-09", count: 0 },
        ],
      },
    });
    render(<Dashboard />);

    expect(await screen.findByRole("list", { name: "形式知化の月次推移" })).toBeInTheDocument();
    expect(screen.queryByText(/まだ形式知化された知識がありません/)).toBeNull();
  });

  it("says so plainly when nothing has been accumulated yet (#294)", async () => {
    getDashboardMock.mockResolvedValue({
      ...DATA,
      knowledge_accumulation: {
        this_month: 0,
        last_month: 0,
        captured_answers: 0,
        consult_retrospectives: 0,
        accepted_handoffs: 0,
        capture_rate: 0,
        monthly: [],
      },
    });
    render(<Dashboard />);

    expect(await screen.findByText(/まだ形式知化された知識がありません/)).toBeInTheDocument();
  });

  it("shows a loading state before data arrives", () => {
    getDashboardMock.mockReturnValue(new Promise(() => {}));
    render(<Dashboard />);
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");
    expect(screen.getByText(/読み込み中/)).toBeInTheDocument();
  });

  it("renders the four headline metrics and the aggregate-only notice", async () => {
    getDashboardMock.mockResolvedValue(DATA);
    render(<Dashboard />);

    expect(await screen.findByRole("heading", { name: "ダッシュボード" })).toBeInTheDocument();
    // aggregate-only privacy notice (product-spec 画面5 principle)
    expect(screen.getByText(/個人の質問内容は表示しません/)).toBeInTheDocument();
    // headline metrics
    expect(screen.getByText("自己解決率")).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument(); // self_resolution_rate
    expect(screen.getByText("上位1名への集中率")).toBeInTheDocument();
    expect(screen.getByText("18%")).toBeInTheDocument(); // top_responder_share
    expect(screen.getByText("平均解決時間")).toBeInTheDocument();
    expect(screen.getByText("3.5 時間")).toBeInTheDocument();
    expect(screen.getByText("推薦精度（最有力）")).toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument(); // eval top1
    // #177: processing-latency KPI (p50 value + p95/sample-size hint).
    expect(screen.getByText("応答速度（中央値）")).toBeInTheDocument();
    expect(screen.getByText("820ms")).toBeInTheDocument(); // p50_ms
    expect(screen.getByText(/95%タイル 2.9s・42件/)).toBeInTheDocument();
    // distributions
    expect(screen.getByText("高梨 健太")).toBeInTheDocument();
    expect(screen.getByText("ネットワーク・VPN")).toBeInTheDocument();
    // #237: feedback-by-stage section (どの段でどれだけずれているか)
    expect(screen.getByText(/フィードバック（AIの解釈・推薦・下書きのズレ/)).toBeInTheDocument();
    expect(screen.getByText("解釈（C1）")).toBeInTheDocument();
    expect(screen.getByText("推薦（C6）")).toBeInTheDocument();
    expect(screen.getByText("下書き（C7）")).toBeInTheDocument();
  });

  it("shows 未計測 for the eval metric and — for resolution time when absent", async () => {
    getDashboardMock.mockResolvedValue({
      ...DATA,
      latest_eval: null,
      avg_resolution_hours: null,
    } satisfies DashboardResponse);
    render(<Dashboard />);

    await screen.findByRole("heading", { name: "ダッシュボード" });
    expect(screen.getByText("未計測")).toBeInTheDocument();
    expect(screen.getByText(/精度評価はまだ実行されていません/)).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument(); // avg_resolution_hours null
  });

  it("shows empty-state text when there are no answers or topics yet", async () => {
    getDashboardMock.mockResolvedValue({
      ...DATA,
      answers_per_responder: [],
      topic_distribution: [],
      feedback_by_stage: { c1: 0, c6: 0, c7: 0, total: 0 },
      knowledge_accumulation: {
        this_month: 0,
        last_month: 0,
        captured_answers: 0,
        consult_retrospectives: 0,
        accepted_handoffs: 0,
        capture_rate: 0,
        monthly: [],
      },
    } satisfies DashboardResponse);
    render(<Dashboard />);

    await screen.findByRole("heading", { name: "ダッシュボード" });
    expect(screen.getByText("まだ回答がありません。")).toBeInTheDocument();
    expect(screen.getByText("まだトピックがありません。")).toBeInTheDocument();
    expect(screen.getByText("まだフィードバックはありません。")).toBeInTheDocument();
  });

  it("shows an error state when the request fails", async () => {
    getDashboardMock.mockRejectedValue(new Error("network"));
    render(<Dashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/取得に失敗/);
  });

  // #369: a non-admin session hitting /dashboard directly (e.g. a stale
  // bookmark from before #347 hid the entry points) gets a 403 that never
  // resolves by retrying, so it needs its own message instead of the
  // generic "time will fix it" error copy.
  it("shows a permission message, not the generic retry message, on a 403", async () => {
    const { ApiError } = await import("@/lib/api-client");
    getDashboardMock.mockRejectedValue(new ApiError(403, "forbidden"));
    render(<Dashboard />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/権限がありません/);
    expect(alert).not.toHaveTextContent(/時間をおいて/);
  });
});
