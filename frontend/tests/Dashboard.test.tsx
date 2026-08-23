import { Dashboard } from "@/components/Dashboard";
import type { DashboardResponse } from "@/lib/api-types";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getDashboardMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getDashboard: (...args: unknown[]) => getDashboardMock(...args),
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
};

describe("Dashboard", () => {
  beforeEach(() => {
    getDashboardMock.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a loading state before data arrives", () => {
    getDashboardMock.mockReturnValue(new Promise(() => {}));
    render(<Dashboard />);
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
    } satisfies DashboardResponse);
    render(<Dashboard />);

    await screen.findByRole("heading", { name: "ダッシュボード" });
    expect(screen.getByText("まだ回答がありません。")).toBeInTheDocument();
    expect(screen.getByText("まだトピックがありません。")).toBeInTheDocument();
  });

  it("shows an error state when the request fails", async () => {
    getDashboardMock.mockRejectedValue(new Error("network"));
    render(<Dashboard />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/取得に失敗/);
  });
});
