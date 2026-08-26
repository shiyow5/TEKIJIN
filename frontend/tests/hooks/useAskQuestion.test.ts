import { useAskQuestion } from "@/hooks/useAskQuestion";
import { isValidSessionId } from "@/lib/session";
import { act, renderHook, waitFor } from "@testing-library/react";
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

const useCurrentUserMock = vi.fn(() => ({
  employees: [],
  currentUserId: "E001" as string | null,
  currentUser: null,
  setCurrentUserId: vi.fn(),
  loading: false,
}));
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

describe("useAskQuestion", () => {
  beforeEach(() => {
    pushMock.mockReset();
    postAskMock.mockReset();
    postAskMock.mockResolvedValue({ session_id: "x", status: "accepted" });
    useCurrentUserMock.mockReturnValue({
      employees: [],
      currentUserId: "E001",
      currentUser: null,
      setCurrentUserId: vi.fn(),
      loading: false,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("gates canSubmit on non-empty trimmed text and a resolved current user", () => {
    const { result } = renderHook(() => useAskQuestion());
    expect(result.current.canSubmit).toBe(false);

    act(() => result.current.setQuestion("   "));
    expect(result.current.canSubmit).toBe(false);

    act(() => result.current.setQuestion("本当の質問"));
    expect(result.current.canSubmit).toBe(true);
  });

  it("stays gated while the current user is unresolved", () => {
    useCurrentUserMock.mockReturnValue({
      employees: [],
      currentUserId: null,
      currentUser: null,
      setCurrentUserId: vi.fn(),
      loading: true,
    });
    const { result } = renderHook(() => useAskQuestion());
    act(() => result.current.setQuestion("質問です"));
    expect(result.current.canSubmit).toBe(false);
  });

  it("submits a trimmed question with a valid session id and asker id, then navigates", async () => {
    const { result } = renderHook(() => useAskQuestion());
    act(() => result.current.setQuestion("  UTMの移行時の注意点  "));
    await act(() => result.current.submit());

    expect(postAskMock).toHaveBeenCalledTimes(1);
    const body = postAskMock.mock.calls[0][0];
    expect(body.question).toBe("UTMの移行時の注意点");
    expect(body.asker_id).toBe("E001");
    expect(isValidSessionId(body.session_id)).toBe(true);

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith(`/session/${body.session_id}`));
  });

  it("calls onSubmitted instead of navigating when the callback is provided", async () => {
    const onSubmitted = vi.fn();
    const { result } = renderHook(() => useAskQuestion(onSubmitted));
    act(() => result.current.setQuestion("質問です"));
    await act(() => result.current.submit());

    expect(onSubmitted).toHaveBeenCalledTimes(1);
    expect(pushMock).not.toHaveBeenCalled();
    expect(isValidSessionId(onSubmitted.mock.calls[0][0])).toBe(true);
  });

  it("sets an error message and re-enables submit when the request fails", async () => {
    postAskMock.mockRejectedValueOnce(new Error("network"));
    const { result } = renderHook(() => useAskQuestion());
    act(() => result.current.setQuestion("質問です"));
    await act(() => result.current.submit());

    expect(result.current.error).toContain("質問の送信に失敗しました");
    expect(result.current.submitting).toBe(false);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("reuses the session id when retrying the same question after a failure", async () => {
    postAskMock.mockRejectedValueOnce(new Error("network"));
    const { result } = renderHook(() => useAskQuestion());
    act(() => result.current.setQuestion("質問です"));
    await act(() => result.current.submit());
    await act(() => result.current.submit());

    expect(postAskMock).toHaveBeenCalledTimes(2);
    expect(postAskMock.mock.calls[1][0].session_id).toBe(postAskMock.mock.calls[0][0].session_id);
  });

  it("navigates instead of erroring when a retry returns 409 (session already running)", async () => {
    const { ApiError } = await import("@/lib/api-client");
    postAskMock.mockRejectedValueOnce(new ApiError(409, "session already has a pending run"));
    const { result } = renderHook(() => useAskQuestion());
    act(() => result.current.setQuestion("質問です"));
    await act(() => result.current.submit());

    expect(pushMock).toHaveBeenCalledTimes(1);
    expect((pushMock.mock.calls[0][0] as string).startsWith("/session/")).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("uses a fresh session id after the question text changes", async () => {
    postAskMock.mockRejectedValueOnce(new Error("network"));
    const { result } = renderHook(() => useAskQuestion());
    act(() => result.current.setQuestion("最初の質問"));
    await act(() => result.current.submit());

    act(() => result.current.setQuestion("別の質問"));
    await act(() => result.current.submit());

    expect(postAskMock).toHaveBeenCalledTimes(2);
    expect(postAskMock.mock.calls[1][0].session_id).not.toBe(
      postAskMock.mock.calls[0][0].session_id,
    );
  });
});
