import { HeroQuestionBar } from "@/components/HeroQuestionBar";
import { QuestionScreen } from "@/components/QuestionScreen";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The guard #421 is actually about: the hub and `/questions` must render the SAME
 * question form.
 *
 * The old test was called "mirrors the /questions heading" but compared only the
 * words, so it stayed green while the heading was 24px on one screen and 30px on
 * the other (#411, the day #392 landed). Comparing the rendered markup catches a
 * class difference, not just a text difference.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const postAskMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  postAsk: (...args: unknown[]) => postAskMock(...args),
  getRecentQuestions: () => new Promise(() => {}),
  ApiError: class ApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => ({
    employees: [],
    currentUserId: "E001",
    currentUser: null,
    setCurrentUserId: vi.fn(),
    loading: false,
    error: false,
    reload: vi.fn(),
    canSwitch: false,
  }),
}));

/**
 * The form subtree only — each screen's own wrapper is allowed to differ.
 *
 * The error paragraph is included even though it renders conditionally: #421 lists
 * 見出し / 入力欄 / 送信ボタン / **エラー表示** as the four pieces that were
 * duplicated, and comparing only the heading and the form would leave the fourth
 * one free to diverge — the exact bug this test exists to prevent.
 *
 * Ids are stripped: React's `useId` derives values from tree position, so the two
 * screens would produce different ids for the SAME component. Without this, adding
 * an id (to wire a real `<label>`, say) fails the test for a reason that has
 * nothing to do with drift, with a diff of two visually identical strings.
 */
function formMarkup(container: HTMLElement): string {
  const heading = container.querySelector("h1");
  const form = container.querySelector("form");
  const alert = container.querySelector('[role="alert"]');
  expect(heading).not.toBeNull();
  expect(form).not.toBeNull();
  const parts = [heading?.outerHTML, form?.outerHTML, alert?.outerHTML ?? "(no error)"];
  return parts.join("\n").replace(/\bid="[^"]*"/g, 'id="[id]"');
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("QuestionForm", () => {
  it("renders identically on the hub and on /questions (#421)", () => {
    const hero = render(<HeroQuestionBar />);
    const heroMarkup = formMarkup(hero.container);
    hero.unmount();

    const screenRender = render(<QuestionScreen />);
    const screenMarkup = formMarkup(screenRender.container);

    expect(heroMarkup).toBe(screenMarkup);
  });

  it("compares the error display too, not just the heading and the form (#421)", async () => {
    // 見出し / 入力欄 / 送信ボタン / エラー表示 are the four pieces #421 names. A
    // review found that a copy diverging ONLY in the error paragraph still passed,
    // so drive both screens into their error state and compare that as well.
    postAskMock.mockRejectedValue(new Error("boom"));

    async function erroredMarkup(ui: React.ReactElement) {
      const view = render(ui);
      fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "テスト" } });
      fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));
      await screen.findByRole("alert");
      const markup = formMarkup(view.container);
      view.unmount();
      return markup;
    }

    expect(await erroredMarkup(<HeroQuestionBar />)).toBe(await erroredMarkup(<QuestionScreen />));
  });

  it("keeps each screen's own wrapper — only the form is shared (#421)", () => {
    // `/questions` adds a back link and 最近のあなたの質問; the hub does not. The
    // extraction must not have flattened those away.
    const hero = render(<HeroQuestionBar />);
    expect(hero.container.querySelector("a")).toBeNull();
    hero.unmount();

    const screenRender = render(<QuestionScreen />);
    expect(screenRender.container.querySelector("a")).not.toBeNull();
  });
});
