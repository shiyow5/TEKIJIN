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
  mockAuth,
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
    await mockAuth(page);
    await mockRecentQuestions(page);
    await page.route(`${API_BASE}/ask`, (route) =>
      fulfillJson(route, { session_id: "srv-session", status: "accepted" }),
    );
    await page.route(`${API_BASE}/events/**`, (route) =>
      fulfillSse(route, sseBody(PERSON_ROUTE_FRAMES)),
    );
    // Confirming the hand-off persists the (possibly edited) draft (#174).
    await page.route(`${API_BASE}/handoff/draft`, (route) =>
      fulfillJson(route, { session_id: "srv-session", status: "draft_saved" }),
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
    await page.getByRole("button", { name: "この内容で依頼する" }).click();

    // Choosing the consultation method is a popup step after the send button.
    const dialog = page.getByRole("dialog", { name: "相談方法を選んでください" });
    await expect(dialog).toBeVisible();
    // It declares aria-modal, so the focus contract has to hold: focus moves in,
    // and Tab cycles inside instead of escaping to the header nav (#245 review).
    await expect(page.getByRole("button", { name: "チャットで相談する" })).toBeFocused();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "キャンセル" })).toBeFocused();
    // Tab from the last button wraps — now to the dialog's own corner close
    // button (ModalDialog, #397 follow-up), the first focusable element in DOM
    // order, not straight back to the first action button.
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "ダイアログを閉じる" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "チャットで相談する" })).toBeFocused();
    await page.getByRole("button", { name: "チャットで相談する" }).click();

    await expect(page.getByRole("heading", { name: "依頼を送りました" })).toBeVisible();
  });

  test("該当者なし → メッセージで終了", async ({ page }) => {
    await mockEmployees(page);
    await mockAuth(page);
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
    await mockAuth(page);
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

  test("prior_answer 経路（単一候補）→ 中間画面を挟まず下書きに到達 (#310)", async ({ page }) => {
    await mockEmployees(page);
    await mockAuth(page);
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

    // Same main-line screen as the multi-candidate case — no intermediate
    // "evidence only" screen, no extra click to reach the draft.
    await expect(
      page.getByRole("heading", { name: "この質問は、人に聞くのが確実です" }),
    ).toBeVisible();
    await expect(page.getByRole("textbox", { name: "聞き方の下書き" })).toHaveValue(
      PERSON_ROUTE_DRAFT,
    );
  });

  test("「最近のあなたの質問」パネルが履歴を表示する", async ({ page }) => {
    await mockEmployees(page);
    await mockAuth(page);
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
    await mockAuth(page);
    await mockRecentQuestions(page, RECENT_QUESTIONS);
    // The re-viewed session replays a (person-route) result over /events.
    await page.route(`${API_BASE}/events/**`, (route) =>
      fulfillSse(route, sseBody(PERSON_ROUTE_FRAMES)),
    );

    await page.goto("/questions");
    await page.getByRole("link", { name: /「UTMの移行時の注意点」/ }).click();

    // #470 moved the destination from the session root to its `/result` view, and
    // left this test asserting the old URL and the ProcessingScreen heading that
    // lives there. Both are now the ResultScreen's: it renders the replayed
    // candidate and draft directly, with no live pipeline in front of it.
    await page.waitForURL(/\/session\/sess-rq1\/result$/);
    await expect(
      page.getByRole("heading", { name: "この質問は、人に聞くのが確実です" }),
    ).toBeVisible();
    // The point of the feature: the SESSION comes back, not just the screen.
    await expect(page.getByRole("heading", { name: /高梨 健太（最有力）/ })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "聞き方の下書き" })).toHaveValue(/高梨さんへ。/);
  });

  test("ホームのヒーロー質問バーから直接送信できる — /questions を経由しない (#392)", async ({
    page,
  }) => {
    await mockEmployees(page);
    await mockAuth(page);
    await mockRecentQuestions(page);
    let askBody: { question?: string; asker_id?: string } | null = null;
    await page.route(`${API_BASE}/ask`, async (route) => {
      askBody = route.request().postDataJSON();
      await fulfillJson(route, { session_id: "srv-session", status: "accepted" });
    });
    await page.route(`${API_BASE}/events/**`, (route) =>
      fulfillSse(route, sseBody(MESSAGE_FRAMES)),
    );

    await page.goto("/");
    await page.getByLabel("質問を入力").fill("有給の繰越ルール");
    await page.getByRole("button", { name: "聞いてみる" }).click();

    // Client-generated session id → straight to /session/<uuid>, same as
    // submitting on /questions itself — never a /questions?q= detour.
    await page.waitForURL(/\/session\/[^/]+$/);
    await expect(page.getByRole("heading", { name: "回答をお届けします" })).toBeVisible();
    expect(askBody?.question).toBe("有給の繰越ルール");
  });
});

/**
 * #392 put the same 「何を知りたいですか？」 heading on the hub as on `/questions`.
 * The unit test asserted the WORDS match; it could not see that the two rendered
 * at different sizes (24px vs 30px) for a while. Compare what the browser
 * actually computes, so "same heading" stays true rather than just true-looking.
 *
 * #421 made both screens render ONE shared `QuestionForm`, so the class strings
 * can no longer disagree and `tests/QuestionForm.test.tsx` pins that structurally.
 * This check is kept because the unit test compares class STRINGS and jsdom
 * applies no stylesheet at all — it never computes a pixel. A wrapper can still
 * restyle its descendants: add `[&_h1]:text-2xl` to either screen's wrapper and
 * the shared component's classes stay byte-identical, the unit test passes, and
 * the two headings render at 24px and 30px again — #411 exactly. (Note it is NOT
 * inheritance: `text-3xl`/`font-bold`/`mb-margin` are all set on the element
 * itself with absolute values, `mb-margin` being a literal 32px.) This is also
 * the only check that Tailwind actually emits these utilities on both routes.
 */
test("the hub's hero heading renders identically to the one on /questions (#392)", async ({
  page,
}) => {
  await mockEmployees(page);
  await mockAuth(page);
  await page.route(`${API_BASE}/notifications*`, (route) => fulfillJson(route, { items: [] }));
  await page.route(`${API_BASE}/questions/recent*`, (route) => fulfillJson(route, []));

  const heading = () => page.getByRole("heading", { name: "何を知りたいですか？" });
  const typography = () =>
    heading().evaluate((el) => {
      const s = getComputedStyle(el);
      return {
        fontSize: s.fontSize,
        fontWeight: s.fontWeight,
        lineHeight: s.lineHeight,
        marginBottom: s.marginBottom,
      };
    });

  await page.goto("/");
  const hub = await typography();
  await page.goto("/questions");
  const questions = await typography();

  expect(hub).toEqual(questions);
});
