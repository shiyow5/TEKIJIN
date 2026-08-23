import { expect, test } from "@playwright/test";
import {
  API_BASE,
  HANDOFF,
  INBOX_ITEM,
  fulfillJson,
  mockEmployees,
  mockInbox,
} from "./support/mocks";

/**
 * Responder discovery journey (#123): /inbox lists the pending handoffs for the
 * current user, and clicking one deep-links to /answer/{session_id} — the route
 * the responder side previously had no in-app way to reach.
 */
test("inbox lists a pending handoff and links to the answer screen", async ({ page }) => {
  await mockEmployees(page);
  await mockInbox(page);
  await page.route(`${API_BASE}/handoff/**`, (route) =>
    fulfillJson(route, { ...HANDOFF, session_id: INBOX_ITEM.session_id }),
  );

  await page.goto("/inbox");

  await expect(page.getByRole("heading", { name: "受信箱" })).toBeVisible();
  await expect(page.getByText(INBOX_ITEM.question)).toBeVisible();
  await expect(page.getByText("藤田 悠斗 さんからの質問")).toBeVisible();

  await page.getByRole("link", { name: /藤田 悠斗 さんからの質問/ }).click();

  await page.waitForURL(new RegExp(`/answer/${INBOX_ITEM.session_id}$`));
  await expect(page.getByRole("heading", { name: "あなたに届いた質問" })).toBeVisible();
});

test("inbox shows an empty state when nothing is pending", async ({ page }) => {
  await mockEmployees(page);
  await mockInbox(page, []);

  await page.goto("/inbox");

  await expect(page.getByText("いまは届いている質問はありません。")).toBeVisible();
});
