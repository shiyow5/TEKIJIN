import { expect, test } from "@playwright/test";
import { API_BASE, HANDOFF, fulfillJson, mockEmployees, mockInbox } from "./support/mocks";

/**
 * Responder journey (#134): /answer/{session_id}. On mount the screen loads the
 * handoff (GET /handoff/{id}); the three choices (引き受ける / 今は難しい / 自分より適任がいる)
 * each POST /answer, and the screen deep-links back to the inbox afterwards. Also
 * covers the "no live handoff" (404) terminal state.
 */

const SESSION_ID = "11111111-1111-4111-8111-111111111111";

function mockHandoff(page: Parameters<typeof mockEmployees>[0]) {
  return page.route(`${API_BASE}/handoff/**`, (route) =>
    fulfillJson(route, { ...HANDOFF, session_id: SESSION_ID }),
  );
}

test.describe("responder flow", () => {
  test("引き受ける で受諾する", async ({ page }) => {
    await mockEmployees(page);
    await mockHandoff(page);
    let answerBody: unknown = null;
    await page.route(`${API_BASE}/answer`, async (route) => {
      answerBody = route.request().postDataJSON();
      await fulfillJson(route, { session_id: SESSION_ID, status: "accepted" });
    });
    await page.route(`${API_BASE}/events/**`, (route) =>
      route.fulfill({ status: 200, contentType: "text/plain", body: "" }),
    );

    await page.goto(`/answer/${SESSION_ID}`);
    await expect(page.getByRole("heading", { name: "あなたに届いた質問" })).toBeVisible();
    await expect(page.getByRole("heading", { name: HANDOFF.question })).toBeVisible();

    await page.getByRole("button", { name: "引き受ける" }).click();
    await expect(page.getByRole("heading", { name: "お引き受けありがとうございます" })).toBeVisible();
    expect(answerBody).toEqual({ session_id: SESSION_ID, outcome: "accepted" });
  });

  test("今は難しい で辞退する", async ({ page }) => {
    await mockEmployees(page);
    await mockHandoff(page);
    let answerBody: unknown = null;
    await page.route(`${API_BASE}/answer`, async (route) => {
      answerBody = route.request().postDataJSON();
      await fulfillJson(route, { session_id: SESSION_ID, status: "accepted" });
    });
    await page.route(`${API_BASE}/events/**`, (route) =>
      route.fulfill({ status: 200, contentType: "text/plain", body: "" }),
    );

    await page.goto(`/answer/${SESSION_ID}`);
    await page.getByRole("button", { name: "今は難しい" }).click();

    await expect(page.getByRole("heading", { name: "承知しました" })).toBeVisible();
    expect(answerBody).toEqual({ session_id: SESSION_ID, outcome: "declined" });
  });

  test("ハンドオフが無い（404）と終了メッセージを出す", async ({ page }) => {
    await mockEmployees(page);
    await page.route(`${API_BASE}/handoff/**`, (route) =>
      fulfillJson(route, { detail: "no responder handoff" }, 404),
    );

    await page.goto(`/answer/${SESSION_ID}`);
    await expect(page.getByRole("heading", { name: "表示できませんでした" })).toBeVisible();
    // getByText, not getByRole("alert") — Next.js's hidden route announcer also
    // has role="alert" (strict-mode ambiguity).
    await expect(page.getByText(/受付を終了/)).toBeVisible();
    await expect(page.getByRole("button", { name: "引き受ける" })).toHaveCount(0);
  });

  test("受諾後に受信箱へ戻れる", async ({ page }) => {
    await mockEmployees(page);
    await mockHandoff(page);
    await mockInbox(page, []);
    await page.route(`${API_BASE}/answer`, (route) =>
      fulfillJson(route, { session_id: SESSION_ID, status: "accepted" }),
    );
    await page.route(`${API_BASE}/events/**`, (route) =>
      route.fulfill({ status: 200, contentType: "text/plain", body: "" }),
    );

    await page.goto(`/answer/${SESSION_ID}`);
    await page.getByRole("button", { name: "引き受ける" }).click();
    await expect(page.getByRole("heading", { name: "お引き受けありがとうございます" })).toBeVisible();

    await page.getByRole("link", { name: "受信箱へ戻る" }).click();
    await page.waitForURL(/\/inbox$/);
    await expect(page.getByRole("heading", { name: "受信箱" })).toBeVisible();
  });
});
