import { HeroQuestionBar } from "@/components/HeroQuestionBar";
import { isValidSessionId } from "@/lib/session";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Submit-flow edge cases (session id reuse, 409 recovery, error messaging)
// live in tests/hooks/useAskQuestion.test.ts, shared with QuestionScreen
// (#392) — this file covers this bar's own rendering and wiring.

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const postAskMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  postAsk: (...args: unknown[]) => postAskMock(...args),
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
  }),
}));

describe("HeroQuestionBar", () => {
  beforeEach(() => {
    pushMock.mockReset();
    postAskMock.mockReset();
    postAskMock.mockResolvedValue({ session_id: "x", status: "accepted" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the question heading", () => {
    // Was called "mirrors the /questions heading" while comparing only the words,
    // so it stayed green through #411's 24px-vs-30px drift. The mirroring claim now
    // belongs to tests/QuestionForm.test.tsx, which compares the rendered markup;
    // this one only says the heading is present.
    render(<HeroQuestionBar />);
    expect(screen.getByRole("heading", { name: "何を知りたいですか？" })).toBeInTheDocument();
  });

  it("disables submit while the input is empty or whitespace", () => {
    render(<HeroQuestionBar />);
    const submit = screen.getByRole("button", { name: "聞いてみる" });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "   " } });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "本当の質問" } });
    expect(submit).toBeEnabled();
  });

  it("submits directly (same as /questions) and navigates to /session/{id} — bypasses /questions", async () => {
    render(<HeroQuestionBar />);
    fireEvent.change(screen.getByLabelText("質問を入力"), {
      target: { value: "  有給の繰越ルール  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));

    await waitFor(() => expect(postAskMock).toHaveBeenCalledTimes(1));
    const body = postAskMock.mock.calls[0][0];
    expect(body.question).toBe("有給の繰越ルール");
    expect(body.asker_id).toBe("E001");
    expect(isValidSessionId(body.session_id)).toBe(true);

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith(`/session/${body.session_id}`));
  });

  it("shows an error message and re-enables submit when the request fails", async () => {
    postAskMock.mockRejectedValueOnce(new Error("network"));
    render(<HeroQuestionBar />);
    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "質問です" } });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("質問の送信に失敗しました");
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "聞いてみる" })).toBeEnabled();
  });
});
