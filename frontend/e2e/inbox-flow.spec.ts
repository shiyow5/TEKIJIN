import { expect, test } from "@playwright/test";
import {
  API_BASE,
  HANDOFF,
  INBOX_ITEM,
  fulfillJson,
  mockAuth,
  mockEmployees,
  mockInbox,
} from "./support/mocks";

/**
 * Responder discovery journey (#123): /inbox lists the pending handoffs for the
 * current user, and shows the first one's full detail — question, selection
 * reason, draft, and the accept/decline actions — right there, with no extra
 * navigation. `/answer/{session_id}` still works as a standalone deep link.
 */
test("inbox lists a pending handoff and shows its detail without an extra click", async ({
  page,
}) => {
  await mockEmployees(page);
  await mockAuth(page);
  await mockInbox(page);
  await page.route(`${API_BASE}/handoff/**`, (route) =>
    fulfillJson(route, { ...HANDOFF, session_id: INBOX_ITEM.session_id }),
  );

  await page.goto("/inbox");

  await expect(page.getByRole("heading", { name: "受信箱" })).toBeVisible();

  // Scope the list assertions to the item's button. The question now appears
  // twice on this page — once in the list, once as the detail pane's heading —
  // and a bare getByText would resolve to both. It races, too: whether the
  // second one exists yet depends on when the handoff fetch lands.
  const listItem = page.getByRole("button", { name: /藤田 悠斗 さんからの質問/ });
  await expect(listItem).toBeVisible();
  await expect(listItem).toContainText(INBOX_ITEM.question);

  // The detail pane for the first (only) item renders automatically.
  await expect(page.getByRole("heading", { name: "あなたに届いた質問" })).toBeVisible();
  await expect(page.getByRole("heading", { name: INBOX_ITEM.question })).toBeVisible();
  await expect(page.getByRole("button", { name: "引き受ける" })).toBeVisible();
  await expect(page.getByRole("button", { name: "今は難しい" })).toBeVisible();
  await expect(page.getByRole("button", { name: "自分より適任がいる" })).toBeVisible();
  expect(page.url()).toContain("/inbox");
});

test("inbox shows an empty state when nothing is pending", async ({ page }) => {
  await mockEmployees(page);
  await mockAuth(page);
  await mockInbox(page, []);

  await page.goto("/inbox");

  await expect(page.getByText("いまは届いている質問はありません。")).toBeVisible();
});
