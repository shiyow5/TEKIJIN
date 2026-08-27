import type { AuthContextValue } from "@/components/AuthProvider";
import { HomeActions } from "@/components/HomeActions";
import type { Principal } from "@/lib/api-types";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const useAuthMock = vi.fn<() => AuthContextValue>();
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => useAuthMock(),
}));

const ADMIN: Principal = { id: null, name: "管理者", dept: null, is_admin: true };
const USER: Principal = { id: "E001", name: "山田 太郎", dept: "営業部", is_admin: false };

function auth(principal: Principal): AuthContextValue {
  return { principal, loading: false, login: vi.fn(), logout: vi.fn(), adoptToken: vi.fn() };
}

afterEach(() => {
  useAuthMock.mockReset();
});

describe("HomeActions", () => {
  it("shows all 3 cards in a 3-col grid for an admin", () => {
    useAuthMock.mockReturnValue(auth(ADMIN));
    render(<HomeActions />);
    expect(screen.getByRole("link", { name: /ダッシュボード/ })).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(list.className).toContain("sm:grid-cols-3");
  });

  // 質問する moved to the hub's own hero question bar (#392) — a duplicate
  // link here would be redundant, so 質問履歴 fills the card slot instead.
  it("offers 質問履歴 instead of a duplicate 質問する link (#392)", () => {
    useAuthMock.mockReturnValue(auth(ADMIN));
    render(<HomeActions />);
    expect(screen.queryByRole("link", { name: /質問する/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /質問履歴/ })).toHaveAttribute("href", "/history");
  });

  // #368: a fixed 3-col grid left a blank column where the dashboard card used
  // to be once #347 hid it for non-admins — switch to a 2-col grid instead so
  // the remaining cards fill the row.
  it("hides the dashboard card and switches to a 2-col grid for a regular user", () => {
    useAuthMock.mockReturnValue(auth(USER));
    render(<HomeActions />);
    expect(screen.queryByRole("link", { name: /ダッシュボード/ })).not.toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(list.className).toContain("sm:grid-cols-2");
    expect(list.className).not.toContain("sm:grid-cols-3");
  });
});
