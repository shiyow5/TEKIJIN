import { HeroQuestionBar } from "@/components/HeroQuestionBar";
import { QuestionScreen } from "@/components/QuestionScreen";
import { render } from "@testing-library/react";
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

vi.mock("@/lib/api-client", () => ({
  postAsk: vi.fn(),
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

/** The form subtree only — each screen's own wrapper is allowed to differ. */
function formMarkup(container: HTMLElement): string {
  const heading = container.querySelector("h1");
  const form = container.querySelector("form");
  expect(heading).not.toBeNull();
  expect(form).not.toBeNull();
  return `${heading?.outerHTML}\n${form?.outerHTML}`;
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
