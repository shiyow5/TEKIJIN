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
  ApiError: class ApiError extends Error {},
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

  it("renders the heading, input, voice button and submit button", () => {
    render(<QuestionScreen />);
    expect(screen.getByRole("heading", { name: "何を知りたいですか？" })).toBeInTheDocument();
    expect(screen.getByLabelText("質問を入力")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /音声入力/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "聞いてみる" })).toBeInTheDocument();
  });

  it("renders the recent-questions list", () => {
    render(<QuestionScreen />);
    expect(screen.getByRole("heading", { name: "最近あなたが解決した質問" })).toBeInTheDocument();
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
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
    expect(body.asker_id).toBe(1);
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
});
