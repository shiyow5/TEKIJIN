import type { Route } from "@playwright/test";

/**
 * Network mocking helpers + fixtures for the E2E suite.
 *
 * The frontend talks to the backend at `NEXT_PUBLIC_API_BASE_URL` (pinned to
 * this value by playwright.config.ts). We intercept those absolute URLs with
 * `page.route(...)` so the tests never need a live backend. Patterns are anchored
 * to the API host so they cannot accidentally match the Next.js page routes
 * (e.g. the `/dashboard` PAGE on :3100 vs the `/dashboard` API call on :8000).
 */
export const API_BASE = "http://localhost:8000";

interface SseFrame {
  event: string;
  data: unknown;
}

/**
 * Build a Server-Sent-Events body from named event frames.
 *
 * A leading `retry` of one hour keeps the browser's native EventSource from
 * reconnecting when a mocked stream ends WITHOUT a terminal event — the asker's
 * person-route view is reached by a non-terminal stream (understood → route →
 * recommend → draft, no `done`). Terminal streams (`done`/`message`/`error`)
 * make the client close the connection itself, so `retry` is harmless there.
 */
export function sseBody(frames: SseFrame[], options: { retryMs?: number } = {}): string {
  const retry = options.retryMs ?? 3_600_000;
  const parts = [`retry: ${retry}\n\n`];
  for (const frame of frames) {
    parts.push(`event: ${frame.event}\ndata: ${JSON.stringify(frame.data)}\n\n`);
  }
  return parts.join("");
}

export async function fulfillSse(route: Route, body: string): Promise<void> {
  await route.fulfill({
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
    },
    body,
  });
}

export async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

// --------------------------------------------------------------------------- //
// Fixtures
// --------------------------------------------------------------------------- //

export const RECOMMENDATION = {
  person_id: "E001",
  name: "高梨 健太",
  dept: "技術部",
  score: 0.92,
  confidence: "高",
  reasons: [{ type: "certification", detail: "情報処理安全確保支援士を保有" }],
};

/** The draft the person-route stream feeds into the editor (asserted verbatim). */
export const PERSON_ROUTE_DRAFT = "高梨さんへ。UTM の移行時に注意すべき点をご相談させてください。";

/** Non-terminal person-route stream → reaches the PersonRouteView result. */
export const PERSON_ROUTE_FRAMES: SseFrame[] = [
  {
    event: "understood",
    data: {
      topics: ["ネットワーク"],
      products: ["UTM"],
      situation: "移行作業",
      question_type: "how",
      confidence: 0.9,
    },
  },
  {
    event: "route",
    data: { route: "person", reason: "同様の案件を直近で担当した方がいます。", confidence: 0.82 },
  },
  { event: "recommend", data: { recommendations: [RECOMMENDATION] } },
  { event: "draft", data: { draft: PERSON_ROUTE_DRAFT } },
];

/** Terminal no-result stream → ProcessingScreen "回答をお届けします" + message. */
export const MESSAGE_FRAMES: SseFrame[] = [
  {
    event: "understood",
    data: {
      topics: ["総務"],
      products: [],
      situation: null,
      question_type: "what",
      confidence: 0.6,
    },
  },
  {
    event: "route",
    data: { route: "document", reason: "該当者が見つかりませんでした。", confidence: 0.3 },
  },
  {
    event: "message",
    data: { status: "no_result", message: "該当する回答が見つかりませんでした。" },
  },
];

export const HANDOFF = {
  question: "UTM の移行時に気をつけることは？",
  asker: { id: 1, name: "山田 太郎", dept: "営業部" },
  topics: ["ネットワーク"],
  products: ["UTM"],
  situation: "移行作業中",
  missing: [],
  responder: RECOMMENDATION,
  draft: "山田さんから UTM 移行の注意点について質問が届いています。",
  reuse_count: 3,
  helpful_answer_count: 5,
};

export const DASHBOARD = {
  total_employees: 42,
  total_questions: 120,
  total_answers: 98,
  recommendation_count: 60,
  recommendation_outcomes: { accepted: 40, declined: 10, pending: 10 },
  acceptance_rate: 0.8,
  self_resolution_rate: 0.25,
  avg_resolution_hours: 4.5,
  top_responder_share: 0.3,
  latest_eval: {
    top1_accuracy: 0.66,
    recall_at_3: 0.59,
    mrr: 0.7,
    route_accuracy: 0.7,
    created_at: "2026-08-22T00:00:00",
  },
  answers_per_responder: [
    { employee_id: 1, name: "高梨 健太", answer_count: 30 },
    { employee_id: 2, name: "鈴木 花子", answer_count: 20 },
  ],
  topic_distribution: [
    { topic: "ネットワーク", count: 50 },
    { topic: "セキュリティ", count: 30 },
  ],
};
