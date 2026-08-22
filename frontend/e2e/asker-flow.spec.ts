import { expect, test } from "@playwright/test";
import {
  API_BASE,
  MESSAGE_FRAMES,
  PERSON_ROUTE_FRAMES,
  fulfillJson,
  fulfillSse,
  sseBody,
} from "./support/mocks";

/**
 * Asker journey: /questions → /session/{id} (processing) → /session/{id}/result.
 * The SSE stream is mocked at the network layer; the person-route stream is
 * deliberately non-terminal so the result screen shows the candidate + draft
 * view rather than the terminal "sent" view.
 */
test.describe("asker flow", () => {
  test("質問 → 処理 → 結果（人に聞く）→ 送信", async ({ page }) => {
    await page.route(`${API_BASE}/ask`, (route) =>
      fulfillJson(route, { session_id: "srv-session", status: "accepted" }),
    );
    await page.route(`${API_BASE}/events/**`, (route) =>
      fulfillSse(route, sseBody(PERSON_ROUTE_FRAMES)),
    );

    await page.goto("/questions");
    await expect(page.getByRole("heading", { name: "何を知りたいですか？" })).toBeVisible();

    await page.getByRole("textbox", { name: "質問を入力" }).fill("UTM の移行時の注意点は？");
    await page.getByRole("button", { name: "聞いてみる" }).click();

    // Client-generated session id → navigates to /session/<uuid>.
    await page.waitForURL(/\/session\/[^/]+$/);

    // Processing screen renders the reasoning steps from the SSE stream.
    await expect(page.getByRole("heading", { name: "回答者が見つかりました" })).toBeVisible();
    await expect(page.getByText("候補を1名見つけました")).toBeVisible();

    await page.getByRole("button", { name: "結果を見る" }).click();
    await page.waitForURL(/\/session\/[^/]+\/result$/);

    await expect(
      page.getByRole("heading", { name: "この質問は、人に聞くのが確実です" }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: /高梨 健太/ })).toBeVisible();

    // The draft event pre-fills the editor, so sending is enabled immediately.
    const draft = page.getByRole("textbox", { name: "聞き方の下書き" });
    await expect(draft).not.toHaveValue("");
    await page.getByRole("button", { name: "この方に送る" }).click();

    await expect(page.getByRole("heading", { name: "送信しました" })).toBeVisible();
  });

  test("該当者なし → メッセージで終了", async ({ page }) => {
    await page.route(`${API_BASE}/ask`, (route) =>
      fulfillJson(route, { session_id: "srv-session", status: "accepted" }),
    );
    await page.route(`${API_BASE}/events/**`, (route) =>
      fulfillSse(route, sseBody(MESSAGE_FRAMES)),
    );

    await page.goto("/questions");
    await page.getByRole("textbox", { name: "質問を入力" }).fill("会社の創立記念日は？");
    await page.getByRole("button", { name: "聞いてみる" }).click();
    await page.waitForURL(/\/session\/[^/]+$/);

    await expect(page.getByRole("heading", { name: "回答をお届けします" })).toBeVisible();
    await expect(page.getByText("該当する回答が見つかりませんでした。")).toBeVisible();
    await expect(page.getByRole("link", { name: "新しい質問をする" })).toBeVisible();
  });
});
