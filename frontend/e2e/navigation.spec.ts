import { type Page, expect, test } from "@playwright/test";
import {
  API_BASE,
  DASHBOARD,
  fulfillJson,
  mockEmployees,
  mockRecentQuestions,
} from "./support/mocks";

/**
 * Cross-cutting navigation (#134): the global header nav, the hub cards, the
 * not-found page, and the current-user switcher's persistence. These are what
 * make the app feel like one app rather than disconnected screens.
 */

// Stub every read the app chrome + landing destinations touch, so navigating
// between them never hits a real backend.
async function mockChrome(page: Page): Promise<void> {
  await mockEmployees(page);
  await mockRecentQuestions(page);
  await page.route(
    (url) => url.href.startsWith(`${API_BASE}/inbox`),
    (route) => fulfillJson(route, { items: [] }),
  );
  await page.route(`${API_BASE}/dashboard`, (route) => fulfillJson(route, DASHBOARD));
}

test.describe("navigation", () => {
  test("header nav moves between screens and marks the current one", async ({ page }) => {
    await mockChrome(page);
    await page.goto("/questions");

    const nav = page.getByRole("navigation", { name: "メインナビゲーション" });
    await nav.getByRole("link", { name: "受信箱" }).click();
    await page.waitForURL(/\/inbox$/);
    await expect(nav.getByRole("link", { name: "受信箱" })).toHaveAttribute("aria-current", "page");

    await nav.getByRole("link", { name: "ダッシュボード" }).click();
    await page.waitForURL(/\/dashboard$/);
    await expect(nav.getByRole("link", { name: "ダッシュボード" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    // The brand links home.
    await page.getByRole("link", { name: /TEKIJIN/ }).click();
    await page.waitForURL(/\/$/);
    await expect(page.getByRole("heading", { level: 1, name: /TEKIJIN/ })).toBeVisible();
  });

  test("hub cards reach the question and inbox screens", async ({ page }) => {
    await mockChrome(page);
    await page.goto("/");

    // "回答する" is unique to the hub card (the nav uses 受信箱).
    await page.getByRole("link", { name: /回答する/ }).click();
    await page.waitForURL(/\/inbox$/);
    await expect(page.getByRole("heading", { name: "受信箱" })).toBeVisible();

    await page
      .getByRole("navigation", { name: "メインナビゲーション" })
      .getByRole("link", { name: "質問する" })
      .click();
    await page.waitForURL(/\/questions$/);
    await expect(page.getByRole("heading", { name: "何を知りたいですか？" })).toBeVisible();
  });

  test("an unknown route shows the not-found page with a way home", async ({ page }) => {
    await mockChrome(page);
    await page.goto("/no-such-page");

    await expect(page.getByRole("heading", { name: "ページが見つかりません" })).toBeVisible();
    await page.getByRole("link", { name: "ホームへ戻る" }).click();
    await page.waitForURL(/\/$/);
    await expect(page.getByRole("heading", { level: 1, name: /TEKIJIN/ })).toBeVisible();
  });

  test("the current-user switcher persists the selection across reloads", async ({ page }) => {
    await mockChrome(page);
    await page.goto("/questions");

    const select = page.getByRole("combobox", { name: "ユーザー切替" });
    await expect(select).toBeEnabled();
    await select.selectOption("E002");

    await page.reload();
    await expect(page.getByRole("combobox", { name: "ユーザー切替" })).toHaveValue("E002");
  });
});
