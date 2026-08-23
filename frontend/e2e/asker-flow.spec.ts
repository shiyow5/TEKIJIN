import { expect, test } from "@playwright/test";
import {
  API_BASE,
  FOLLOWUP_FRAMES,
  MESSAGE_FRAMES,
  PERSON_ROUTE_DRAFT,
  PERSON_ROUTE_FRAMES,
  PRIOR_ANSWER_FRAMES,
  RECENT_QUESTIONS,
  fulfillJson,
  fulfillSse,
  mockEmployees,
  mockRecentQuestions,
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
    await mockEmployees(page);
    await mockRecentQuestions(page);
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

    // The draft event pre-fills the editor verbatim, so sending is enabled at once.
    const draft = page.getByRole("textbox", { name: "聞き方の下書き" });
    await expect(draft).toHaveValue(PERSON_ROUTE_DRAFT);
    await page.getByRole("button", { name: "この方に送る" }).click();

    await expect(page.getByRole("heading", { name: "送信しました" })).toBeVisible();
  });

  test("該当者なし → メッセージで終了", async ({ page }) => {
    await mockEmployees(page);
    await mockRecentQuestions(page);
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

  test("逆質問（followup）→ 補足を回答", async ({ page }) => {
    await mockEmployees(page);
    await mockRecentQuestions(page);
    await page.route(`${API_BASE}/ask`, (route) =>
      fulfillJson(route, { session_id: "srv-session", status: "accepted" }),
    );
    await page.route(`${API_BASE}/events/**`, (route) =>
      fulfillSse(route, sseBody(FOLLOWUP_FRAMES)),
    );
    let answerBody: { reply?: string } | null = null;
    await page.route(`${API_BASE}/answer`, async (route) => {
      answerBody = route.request().postDataJSON();
      await fulfillJson(route, { session_id: "srv-session", status: "accepted" });
    });

    await page.goto("/questions");
    await page.getByRole("textbox", { name: "質問を入力" }).fill("ネットワークの相談です");
    await page.getByRole("button", { name: "聞いてみる" }).click();
    await page.waitForURL(/\/session\/[^/]+$/);

    // The AI asks a clarifying question; the reply box appears.
    await expect(page.getByText("確認させてください")).toBeVisible();
    await page.getByRole("textbox", { name: "補足の回答" }).fill("現行はVPN機器で3拠点です");
    await page.getByRole("button", { name: "回答する" }).click();

    await expect.poll(() => answerBody?.reply).toBe("現行はVPN機器で3拠点です");
  });

  test("prior_answer 経路 → 詳しい人の提示 → 解決", async ({ page }) => {
    await mockEmployees(page);
    await mockRecentQuestions(page);
    await page.route(`${API_BASE}/ask`, (route) =>
      fulfillJson(route, { session_id: "srv-session", status: "accepted" }),
    );
    await page.route(`${API_BASE}/events/**`, (route) =>
      fulfillSse(route, sseBody(PRIOR_ANSWER_FRAMES)),
    );

    await page.goto("/questions");
    await page.getByRole("textbox", { name: "質問を入力" }).fill("VPNの設定について");
    await page.getByRole("button", { name: "聞いてみる" }).click();
    await page.waitForURL(/\/session\/[^/]+$/);

    await expect(page.getByRole("heading", { name: "回答者が見つかりました" })).toBeVisible();
    await page.getByRole("button", { name: "結果を見る" }).click();
    await page.waitForURL(/\/session\/[^/]+\/result$/);

    // The prior-answer view presents the expert as evidence, not the answer.
    await expect(page.getByRole("heading", { name: /詳しそうです/ })).toBeVisible();
    await page.getByRole("button", { name: "解決した" }).click();
    await expect(page.getByRole("heading", { name: "解決しました" })).toBeVisible();
  });

  test("「最近のあなたの質問」パネルが履歴を表示する", async ({ page }) => {
    await mockEmployees(page);
    await mockRecentQuestions(page, RECENT_QUESTIONS);

    await page.goto("/questions");

    await expect(page.getByRole("heading", { name: "最近のあなたの質問" })).toBeVisible();
    await expect(page.getByText("UTMの移行時の注意点")).toBeVisible();
    await expect(page.getByText("解決済").first()).toBeVisible();
    await expect(page.getByText("対応中")).toBeVisible();
    // A document-route question is self-resolved, not "取り次ぎ先を調整中" (#142).
    await expect(page.getByText("社内文書で回答")).toBeVisible();
    await expect(page.getByText("取り次ぎ先を調整中です。")).toBeVisible(); // only the pending one
  });

  test("履歴の項目をクリックすると結果セッションを再表示できる (#150)", async ({ page }) => {
    await mockEmployees(page);
    await mockRecentQuestions(page, RECENT_QUESTIONS);
    // The re-viewed session replays a (person-route) result over /events.
    await page.route(`${API_BASE}/events/**`, (route) =>
      fulfillSse(route, sseBody(PERSON_ROUTE_FRAMES)),
    );

    await page.goto("/questions");
    await page.getByRole("link", { name: /「UTMの移行時の注意点」の結果をもう一度見る/ }).click();

    await page.waitForURL(/\/session\/sess-rq1$/);
    // The replayed result renders (candidate + draft on the processing screen).
    await expect(page.getByText("回答者が見つかりました")).toBeVisible();
  });
});
