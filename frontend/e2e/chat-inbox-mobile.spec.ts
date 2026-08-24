import { type Page, expect, test } from "@playwright/test";
import {
  API_BASE,
  INBOX_ITEM,
  fulfillJson,
  mockAuth,
  mockEmployees,
  mockInbox,
  mockRecentQuestions,
} from "./support/mocks";

/**
 * Phone-width behaviour of the two split views (#254 の流儀). Below `md` the
 * list and the detail take turns rather than sitting side by side — at 390px
 * two panes overflowed the viewport (chat by 288px, the inbox by 106px) and the
 * detail collapsed to an unreadable column.
 */

const THREADS = [
  {
    thread_id: 1,
    question_id: "q1",
    question_title: "UTMのフィルタ設定について",
    counterpart: { id: "E017", name: "高梨 健太", dept: "技術部" },
    last_message: "承知しました。",
    last_message_at: "2026-08-24T10:12:00",
    created_at: "2026-08-24T09:00:00",
  },
];
const DETAIL = {
  thread_id: 1,
  question_id: "q1",
  question_title: "UTMのフィルタ設定について",
  counterpart: { id: "E017", name: "高梨 健太", dept: "技術部" },
  messages: [
    {
      id: 1,
      thread_id: 1,
      sender_id: "E001",
      body: "教えてください。",
      created_at: "2026-08-24T09:01:00",
    },
  ],
};

async function mockChat(page: Page): Promise<void> {
  await mockEmployees(page);
  await mockAuth(page);
  await mockRecentQuestions(page);
  await page.route(
    (u) => u.href.startsWith(API_BASE) && /^\/messages\/threads$/.test(u.pathname),
    (r) => fulfillJson(r, { items: THREADS }),
  );
  await page.route(
    (u) => u.href.startsWith(API_BASE) && /^\/messages\/threads\/\d+$/.test(u.pathname),
    (r) => fulfillJson(r, DETAIL),
  );
}

async function overflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test.describe("phone-width split views (#254)", () => {
  test("chat shows the list, then the conversation with a way back", async ({ page }) => {
    await mockChat(page);
    await page.setViewportSize({ width: 390, height: 780 });
    await page.goto("/chat");

    const list = page.getByRole("list", { name: "チャットスレッド一覧" });
    await expect(list).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(0);

    await list.getByRole("button", { name: /高梨 健太/ }).click();
    await expect(page.getByRole("textbox", { name: /メッセージ/ })).toBeVisible();
    await expect(list).toBeHidden();
    expect(await overflow(page)).toBeLessThanOrEqual(0);

    await page.getByRole("button", { name: "← 一覧へ戻る" }).click();
    await expect(list).toBeVisible();
  });

  test("inbox shows the list, then the question with a way back", async ({ page }) => {
    await mockEmployees(page);
    await mockAuth(page);
    await mockInbox(page, [INBOX_ITEM]);
    await page.route(
      (u) => u.href.startsWith(`${API_BASE}/handoff/`),
      (r) => fulfillJson(r, { detail: "gone" }, 404),
    );
    await page.setViewportSize({ width: 390, height: 780 });
    await page.goto("/inbox");

    const first = page.getByRole("button", { name: /さんからの質問/ }).first();
    await expect(first).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(0);

    await first.click();
    await expect(page.getByRole("button", { name: "← 受信箱へ戻る" })).toBeVisible();
    await expect(first).toBeHidden();
    expect(await overflow(page)).toBeLessThanOrEqual(0);
  });

  test("desktop keeps both panes side by side", async ({ page }) => {
    await mockChat(page);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/chat");
    await expect(page.getByRole("list", { name: "チャットスレッド一覧" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: /メッセージ/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "← 一覧へ戻る" })).toBeHidden();
    expect(await overflow(page)).toBeLessThanOrEqual(0);
  });
});
