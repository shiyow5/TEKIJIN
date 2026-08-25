import { type Page, expect, test } from "@playwright/test";
import {
  API_BASE,
  DASHBOARD,
  fulfillJson,
  mockAuth,
  mockEmployees,
  mockRecentQuestions,
} from "./support/mocks";

/**
 * Cross-cutting navigation (#134): the global header nav, the hub cards, the
 * not-found page, and the current-user switcher — both that it persists and that
 * switching returns to the hub (#210). These are what make the app feel like one
 * app rather than disconnected screens.
 */

// Stub every read the app chrome + landing destinations touch, so navigating
// between them never hits a real backend.
async function mockChrome(page: Page): Promise<void> {
  await mockEmployees(page);
  await mockAuth(page);
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

    const select = page.getByRole("combobox", { name: /利用者を切替/ });
    await expect(select).toBeEnabled();
    await select.selectOption("E002");

    // Switching leaves for the hub (#210); wait for it so the reload below has a
    // settled URL rather than racing the client-side navigation.
    await page.waitForURL(/\/$/);
    await page.reload();
    await expect(page.getByRole("combobox", { name: /利用者を切替/ })).toHaveValue("E002");
  });

  test("the header background spans the full viewport above max-w-content (#250)", async ({
    page,
  }) => {
    await mockChrome(page);
    // 1920 > the 1440px `max-w-content`: the regression was the <header> itself
    // being capped there, letting the body's tinted background show beside it.
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto("/questions");
    await expect(page.getByRole("navigation", { name: "メインナビゲーション" })).toBeVisible();

    const { headerWidth, viewportWidth, navRight } = await page.evaluate(() => {
      const header = document.querySelector("header") as HTMLElement;
      const nav = document.querySelector('nav[aria-label="メインナビゲーション"]') as HTMLElement;
      return {
        headerWidth: header.getBoundingClientRect().width,
        viewportWidth: document.documentElement.clientWidth,
        navRight: nav.getBoundingClientRect().right,
      };
    });

    expect(headerWidth).toBe(viewportWidth);
    // ...while the CONTENT stays centred: the nav must not run past the
    // centred 1440px column, or we have merely widened everything.
    expect(navRight).toBeLessThanOrEqual((viewportWidth - 1440) / 2 + 1440 + 1);
  });

  test("mobile width collapses the nav behind a hamburger (#254)", async ({ page }) => {
    await mockChrome(page);
    await page.setViewportSize({ width: 375, height: 780 });
    await page.goto("/questions");

    // The desktop nav is display:none below `md`, so its links are not reachable.
    const nav = page.getByRole("navigation", { name: "メインナビゲーション" });
    await expect(nav).toBeHidden();

    const toggle = page.getByRole("button", { name: "メニューを開く" });
    await expect(toggle).toBeVisible();
    await toggle.click();

    const menu = page.locator("#mobile-nav-menu");
    await expect(menu.getByRole("link", { name: "受信箱" })).toBeVisible();
    await menu.getByRole("link", { name: "受信箱" }).click();
    await page.waitForURL(/\/inbox$/);
    // Navigating closes it.
    await expect(page.locator("#mobile-nav-menu")).toHaveCount(0);

    // Nothing overflows the viewport at phone width (the崩れ #254 reports).
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("desktop width keeps the inline nav and no hamburger (#254)", async ({ page }) => {
    await mockChrome(page);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/questions");

    await expect(
      page.getByRole("navigation", { name: "メインナビゲーション" }).getByRole("link", {
        name: "受信箱",
      }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /メニューを(開く|閉じる)/ })).toBeHidden();
  });

  test("the notification panel stays inside the viewport at phone width (#316)", async ({
    page,
  }) => {
    await mockChrome(page);
    // Below `md` the header row wraps (#288) and the bell becomes the leftmost
    // item, no longer near the right edge — the regression was the panel still
    // opening right-anchored and landing mostly off the left edge of the screen.
    await page.setViewportSize({ width: 390, height: 780 });
    await page.goto("/questions");

    await page.getByRole("button", { name: "通知" }).click();
    const panel = page.getByRole("menu", { name: "通知一覧" });
    await expect(panel).toBeVisible();

    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.x).toBeGreaterThanOrEqual(0);
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390);
  });

  test("the notification panel stays right-anchored at desktop width (#316)", async ({ page }) => {
    await mockChrome(page);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/questions");

    await page.getByRole("button", { name: "通知" }).click();
    const panel = page.getByRole("menu", { name: "通知一覧" });
    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(1280);
  });

  test("switching the current user returns to the hub (#210)", async ({ page }) => {
    await mockChrome(page);
    // The inbox is the screen the bug was reported on: it re-fetches for the new
    // user, so the data was right while the screen still said "受信箱".
    await page.goto("/inbox");
    await expect(page.getByRole("heading", { name: "受信箱" })).toBeVisible();

    await page.getByRole("combobox", { name: /利用者を切替/ }).selectOption("E002");

    await page.waitForURL(/\/$/);
    await expect(page.getByRole("heading", { level: 1, name: /TEKIJIN/ })).toBeVisible();
    await expect(page.getByRole("combobox", { name: /利用者を切替/ })).toHaveValue("E002");
  });
});
