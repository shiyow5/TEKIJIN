import { expect, test } from "@playwright/test";
import { API_BASE, fulfillJson, mockEmployees } from "./support/mocks";

/**
 * Document viewer (#143): the `/documents/[id]` route that the document-route
 * terminal links to. We stub GET /documents/{id} so the page renders without a
 * live backend.
 */

const DOC = {
  id: "doc_001",
  title: "社内IT・ヘルプデスク手順書",
  body: "PCセットアップは標準イメージ＋キッティング手順書を用意する。",
  source: "社内Wiki/IT",
  updated_at: "2026-08-01T09:00:00",
};

test.describe("document viewer", () => {
  test("renders the cited document's title, body and metadata", async ({ page }) => {
    await mockEmployees(page);
    await page.route(`${API_BASE}/documents/doc_001`, (route) => fulfillJson(route, DOC));

    await page.goto("/documents/doc_001");

    await expect(page.getByRole("heading", { name: DOC.title })).toBeVisible();
    await expect(page.getByText(/キッティング手順書/)).toBeVisible();
    await expect(page.getByText("出典: 社内Wiki/IT")).toBeVisible();
    // A way back to asking.
    await expect(page.getByRole("link", { name: /戻る/ })).toBeVisible();
  });

  test("shows a not-found state for an unknown document", async ({ page }) => {
    await mockEmployees(page);
    await page.route(`${API_BASE}/documents/nope`, (route) =>
      fulfillJson(route, { detail: "document not found" }, 404),
    );

    await page.goto("/documents/nope");

    await expect(page.getByRole("heading", { name: "文書が見つかりません" })).toBeVisible();
  });
});
