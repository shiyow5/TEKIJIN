import HomePage from "@/../app/page";
import type { AuthContextValue } from "@/components/AuthProvider";
import type { Principal } from "@/lib/api-types";
import { isValidSessionId } from "@/lib/session";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useAuthMock = vi.fn<() => AuthContextValue>();
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => useAuthMock(),
}));

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

// The hero bar submits directly via useAskQuestion (#392) — same mocks as
// HeroQuestionBar.test.tsx / QuestionScreen.test.tsx.
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

const ADMIN: Principal = { id: null, name: "管理者", dept: null, is_admin: true };
const USER: Principal = { id: "E001", name: "山田 太郎", dept: "営業部", is_admin: false };

function auth(principal: Principal): AuthContextValue {
  return { principal, loading: false, login: vi.fn(), logout: vi.fn() };
}

beforeEach(() => {
  // Admin by default; individual tests override for the non-admin case (#347).
  useAuthMock.mockReturnValue(auth(ADMIN));
  pushMock.mockReset();
  postAskMock.mockReset();
  postAskMock.mockResolvedValue({ session_id: "x", status: "accepted" });
});

afterEach(() => {
  useAuthMock.mockReset();
});

describe("HomePage (hub)", () => {
  it("links only to real, existing routes (#121: no /results or /answers 404s)", () => {
    render(<HomePage />);
    const hrefs = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"))
      .filter(Boolean);

    expect(hrefs).toContain("/history");
    expect(hrefs).toContain("/inbox");
    expect(hrefs).toContain("/dashboard");
    // The old placeholder dead links must be gone.
    expect(hrefs).not.toContain("/results");
    expect(hrefs).not.toContain("/answers");
  });

  it("shows the dashboard card to an admin but hides it from a regular user (#347)", () => {
    useAuthMock.mockReturnValue(auth(ADMIN));
    const { rerender } = render(<HomePage />);
    expect(screen.getByRole("link", { name: /ダッシュボード/ })).toHaveAttribute(
      "href",
      "/dashboard",
    );

    useAuthMock.mockReturnValue(auth(USER));
    rerender(<HomePage />);
    expect(screen.queryByRole("link", { name: /ダッシュボード/ })).toBeNull();
    // The other action cards stay available to a regular user.
    expect(screen.getByRole("link", { name: /回答する/ })).toBeInTheDocument();
  });

  it("no longer describes itself as a placeholder", () => {
    render(<HomePage />);
    expect(screen.queryByText(/プレースホルダ/)).toBeNull();
  });

  it("opens directly on the hero question bar mirroring /questions (#392)", () => {
    render(<HomePage />);
    // The standalone "TEKIJIN" title + description is gone; the question bar's
    // own heading is now the page's h1.
    expect(screen.queryByRole("heading", { name: "TEKIJIN" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "何を知りたいですか？" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("質問を入力")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "聞いてみる" })).toBeInTheDocument();
    // 質問する moved off the action cards (#392) — 質問履歴 fills that slot instead.
    expect(screen.queryByRole("link", { name: /質問する/ })).toBeNull();
    expect(screen.getByRole("link", { name: /質問履歴/ })).toHaveAttribute("href", "/history");
  });

  it("submits directly from the hero bar to /session/{id} — no /questions detour (#392)", async () => {
    render(<HomePage />);
    fireEvent.change(screen.getByLabelText("質問を入力"), {
      target: { value: "退職金の計算方法" },
    });
    fireEvent.click(screen.getByRole("button", { name: "聞いてみる" }));

    await waitFor(() => expect(postAskMock).toHaveBeenCalledTimes(1));
    const body = postAskMock.mock.calls[0][0];
    expect(body.question).toBe("退職金の計算方法");
    expect(body.asker_id).toBe("E001");
    expect(isValidSessionId(body.session_id)).toBe(true);

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith(`/session/${body.session_id}`));
  });

  it("no longer claims the answer source is always a person (#292/#324)", () => {
    render(<HomePage />);
    expect(screen.queryByText("回答の出所は、常に人。")).toBeNull();
    expect(screen.queryByText(/AIは代わりに答えません/)).toBeNull();
  });

  // The always-visible "使い方" strip moved behind a "？" button (#392); the
  // content itself (incl. the #337 framing note) is unchanged.
  it("hides 使い方 behind a button and shows it in a dialog once opened (#392, #337)", () => {
    render(<HomePage />);
    expect(screen.queryByRole("heading", { name: "使い方" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "使い方を見る" }));

    expect(screen.getByRole("heading", { name: "使い方" })).toBeInTheDocument();
    expect(screen.getByText(/一部はAIの直接回答に置き換わっていきます/)).toBeInTheDocument();
  });
});
