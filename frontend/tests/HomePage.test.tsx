import HomePage from "@/../app/page";
import type { AuthContextValue } from "@/components/AuthProvider";
import type { Principal } from "@/lib/api-types";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useAuthMock = vi.fn<() => AuthContextValue>();
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => useAuthMock(),
}));

const ADMIN: Principal = { id: null, name: "管理者", dept: null, is_admin: true };
const USER: Principal = { id: "E001", name: "山田 太郎", dept: "営業部", is_admin: false };

function auth(principal: Principal): AuthContextValue {
  return { principal, loading: false, login: vi.fn(), logout: vi.fn() };
}

beforeEach(() => {
  // Admin by default; individual tests override for the non-admin case (#347).
  useAuthMock.mockReturnValue(auth(ADMIN));
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

    expect(hrefs).toContain("/questions");
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

  it("presents the product promise, a primary CTA, and a how-it-works strip", () => {
    render(<HomePage />);
    expect(screen.getByRole("heading", { level: 1, name: /TEKIJIN/ })).toBeInTheDocument();
    // The hero CTA and the "質問する" action card both link to /questions.
    const ask = screen.getAllByRole("link", { name: /質問する/ });
    expect(ask.length).toBeGreaterThanOrEqual(1);
    expect(ask.every((a) => a.getAttribute("href") === "/questions")).toBe(true);
    expect(screen.getByRole("heading", { name: "使い方" })).toBeInTheDocument();
  });

  it("no longer claims the answer source is always a person (#292/#324)", () => {
    render(<HomePage />);
    expect(screen.queryByText("回答の出所は、常に人。")).toBeNull();
    expect(screen.queryByText(/AIは代わりに答えません/)).toBeNull();
  });

  it("frames the how-it-works flow as today's shape, not the only one (#337)", () => {
    render(<HomePage />);
    // The 3-step strip itself still describes today's live flow ("a person
    // answers") — this note keeps it from reading as the only possible one.
    expect(screen.getByText(/この3ステップは今のかたちです/)).toBeInTheDocument();
  });
});
