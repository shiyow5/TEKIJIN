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
  return { principal, loading: false, login: vi.fn(), logout: vi.fn() };
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
