import { expect, test } from "@playwright/test";
import { API_BASE, DASHBOARD, fulfillJson } from "./support/mocks";

/**
 * Dashboard (画面5) smoke test: the page fetches GET /dashboard on mount and
 * renders the aggregate-only view. Asserts the page reaches its loaded state
 * (not the loading/error fallbacks) and shows the privacy notice.
 */
test("dashboard renders the aggregate view", async ({ page }) => {
  await page.route(`${API_BASE}/dashboard`, (route) => fulfillJson(route, DASHBOARD));

  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "ダッシュボード", exact: true })).toBeVisible();
  await expect(
    page.getByText("個人の質問内容は表示しません。集計のみを表示します。"),
  ).toBeVisible();
  await expect(page.getByText("自己解決率")).toBeVisible();
});
