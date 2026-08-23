import { expect, test } from "@playwright/test";
import { API_BASE, DASHBOARD, fulfillJson, mockEmployees } from "./support/mocks";

/**
 * Dashboard (画面5, #134): the loaded aggregate view and the error fallback.
 */
test.describe("dashboard", () => {
  test("renders the aggregate view", async ({ page }) => {
    await mockEmployees(page);
    await page.route(`${API_BASE}/dashboard`, (route) => fulfillJson(route, DASHBOARD));

    await page.goto("/dashboard");

    await expect(page.getByRole("heading", { name: "ダッシュボード", exact: true })).toBeVisible();
    await expect(
      page.getByText("個人の質問内容は表示しません。集計のみを表示します。"),
    ).toBeVisible();
    await expect(page.getByText("自己解決率")).toBeVisible();
  });

  test("shows an error fallback when the aggregate fetch fails", async ({ page }) => {
    await mockEmployees(page);
    await page.route(`${API_BASE}/dashboard`, (route) =>
      fulfillJson(route, { detail: "boom" }, 500),
    );

    await page.goto("/dashboard");

    await expect(page.getByRole("heading", { name: "表示できませんでした" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("集計データの取得に失敗しました");
  });
});
