import { expect, test } from "@playwright/test";
import { API_BASE, HANDOFF, fulfillJson, mockEmployees } from "./support/mocks";

/**
 * Responder journey: /answer/{session_id}. On mount the screen loads the handoff
 * (GET /handoff/{id}); pressing 回答する submits POST /answer with
 * outcome="accepted" and then drains GET /events/{id} (advanceSession) before
 * showing the confirmation.
 */
test("responder answers the handoff", async ({ page }) => {
  const sessionId = "11111111-1111-4111-8111-111111111111";

  await mockEmployees(page);
  await page.route(`${API_BASE}/handoff/**`, (route) =>
    fulfillJson(route, { ...HANDOFF, session_id: sessionId }),
  );

  let answerBody: unknown = null;
  await page.route(`${API_BASE}/answer`, async (route) => {
    answerBody = route.request().postDataJSON();
    await fulfillJson(route, { session_id: sessionId, status: "accepted" });
  });

  // advanceSession() issues a plain GET /events/{id} and drains the body.
  await page.route(`${API_BASE}/events/**`, (route) =>
    route.fulfill({ status: 200, contentType: "text/plain", body: "" }),
  );

  await page.goto(`/answer/${sessionId}`);

  await expect(page.getByRole("heading", { name: "あなたに届いた質問" })).toBeVisible();
  await expect(page.getByRole("heading", { name: HANDOFF.question })).toBeVisible();

  await page.getByRole("button", { name: "回答する" }).click();

  await expect(page.getByRole("heading", { name: "回答ありがとうございます" })).toBeVisible();
  expect(answerBody).toEqual({ session_id: sessionId, outcome: "accepted" });
});
