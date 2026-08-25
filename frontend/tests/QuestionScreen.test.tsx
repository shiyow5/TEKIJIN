import { QuestionScreen } from "@/components/QuestionScreen";
import { isValidSessionId } from "@/lib/session";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

// The acting user comes from the current-user context; a fixed "E001" stands in
// for the header switcher's selection.
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => ({
    employees: [],
    currentUserId: "E001",
    currentUser: null,
    setCurrentUserId: vi.fn(),
    loading: false,
  }),
}));

// RecentQuestions self-fetches; stub it so these tests stay focused on the ask flow.
vi.mock("@/components/RecentQuestions", () => ({
  RecentQuestions: () => <div data-testid="recent-questions" />,
}));

describe("QuestionScreen", () => {
  beforeEach(() => {
    pushMock.mockReset();
    postAskMock.mockReset();
    postAskMock.mockResolvedValue({ session_id: "x", status: "accepted" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the heading, input and submit button", () => {
    render(<QuestionScreen />);
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("heading", { name: "何を知りたいですか？" })).toBeInTheDocument();
    expect(screen.getByLabelText("質問を入力")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "聞いてみる" })).toBeInTheDocument();
  });

  it("renders the recent-questions panel", () => {
    render(<QuestionScreen />);
    expect(screen.getByTestId("recent-questions")).toBeInTheDocument();
  });

  it("disables the submit button while the input is empty or whitespace", () => {
    render(<QuestionScreen />);
    const submit = screen.getByRole("button", { name: "聞いてみる" });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "   " } });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "本当の質問" } });
    expect(submit).toBeEnabled();
  });

  it("submits a trimmed question with a valid session id and asker id, then navigates", async () => {
    render(<QuestionScreen />);
    fireEvent.change(screen.getByLabelText("質問を入力"), {
      target: { value: "  UTMの移行時の注意点  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));

    await waitFor(() => expect(postAskMock).toHaveBeenCalledTimes(1));
    const body = postAskMock.mock.calls[0][0];
    expect(body.question).toBe("UTMの移行時の注意点");
    expect(body.asker_id).toBe("E001");
    expect(isValidSessionId(body.session_id)).toBe(true);

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith(`/session/${body.session_id}`));
  });

  it("calls onSubmitted instead of navigating when the callback is provided", async () => {
    const onSubmitted = vi.fn();
    render(<QuestionScreen onSubmitted={onSubmitted} />);
    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "質問です" } });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));

    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(1));
    expect(pushMock).not.toHaveBeenCalled();
    expect(isValidSessionId(onSubmitted.mock.calls[0][0])).toBe(true);
  });

  it("shows an error message and re-enables submit when the request fails", async () => {
    postAskMock.mockRejectedValueOnce(new Error("network"));
    render(<QuestionScreen />);
    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "質問です" } });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("質問の送信に失敗しました");
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "聞いてみる" })).toBeEnabled();
  });

  it("reuses the session id when retrying the same question after a failure", async () => {
    postAskMock.mockRejectedValueOnce(new Error("network"));
    render(<QuestionScreen />);
    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "質問です" } });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));
    await screen.findByRole("alert");

    // Retry without changing the question — the second /ask must carry the same id.
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));
    await waitFor(() => expect(postAskMock).toHaveBeenCalledTimes(2));

    const first = postAskMock.mock.calls[0][0].session_id;
    const second = postAskMock.mock.calls[1][0].session_id;
    expect(second).toBe(first);
  });

  it("navigates instead of erroring when a retry returns 409 (session already running)", async () => {
    const { ApiError } = await import("@/lib/api-client");
    postAskMock.mockRejectedValueOnce(new ApiError(409, "session already has a pending run"));
    render(<QuestionScreen />);
    fireEvent.change(screen.getByLabelText("質問を入力"), { target: { value: "質問です" } });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));

    await waitFor(() => expect(pushMock).toHaveBeenCalledTimes(1));
    const target = pushMock.mock.calls[0][0] as string;
    expect(target.startsWith("/session/")).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("uses a fresh session id after the question text changes", async () => {
    postAskMock.mockRejectedValueOnce(new Error("network"));
    render(<QuestionScreen />);
    const input = screen.getByLabelText("質問を入力");
    fireEvent.change(input, { target: { value: "最初の質問" } });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));
    await screen.findByRole("alert");

    fireEvent.change(input, { target: { value: "別の質問" } });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));
    await waitFor(() => expect(postAskMock).toHaveBeenCalledTimes(2));

    expect(postAskMock.mock.calls[1][0].session_id).not.toBe(
      postAskMock.mock.calls[0][0].session_id,
    );
  });
});
