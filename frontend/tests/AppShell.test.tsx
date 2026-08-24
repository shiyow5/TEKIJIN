import { AppShell } from "@/components/AppShell";
import type { AuthContextValue } from "@/components/AuthProvider";
import type { Principal } from "@/lib/api-types";
import { render, screen, waitFor } from "@testing-library/react";
import { type ReactNode, act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useAuthMock = vi.fn<() => AuthContextValue>();
// AuthProvider is a pass-through here; useAuth is controlled per test.
vi.mock("@/components/AuthProvider", () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useAuth: () => useAuthMock(),
}));
vi.mock("@/components/CurrentUserProvider", () => ({
  CurrentUserProvider: ({ children }: { children: ReactNode }) => (
    <div data-testid="current-user-provider">{children}</div>
  ),
}));
vi.mock("@/components/AppHeader", () => ({
  AppHeader: () => <header data-testid="app-header" />,
}));

const pathnameMock = vi.fn<() => string>();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => pathnameMock(),
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

const USER: Principal = { id: "E001", name: "山田", dept: null, is_admin: false };

function auth(partial: Partial<AuthContextValue>): AuthContextValue {
  return { principal: null, loading: false, login: vi.fn(), logout: vi.fn(), ...partial };
}

beforeEach(() => {
  pathnameMock.mockReturnValue("/");
});

afterEach(() => {
  useAuthMock.mockReset();
  pathnameMock.mockReset();
  replaceMock.mockReset();
});

function renderShell() {
  return act(() => {
    render(
      <AppShell>
        <div data-testid="page">page</div>
      </AppShell>,
    );
  });
}

describe("AppShell / AuthGate", () => {
  it("renders the app chrome and children when authenticated", async () => {
    useAuthMock.mockReturnValue(auth({ principal: USER }));
    await renderShell();
    expect(screen.getByTestId("app-header")).toBeInTheDocument();
    expect(screen.getByTestId("current-user-provider")).toBeInTheDocument();
    expect(screen.getByTestId("page")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("redirects to /login when unauthenticated on a protected route", async () => {
    useAuthMock.mockReturnValue(auth({ principal: null }));
    await renderShell();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(screen.queryByTestId("app-header")).not.toBeInTheDocument();
  });

  it("shows a loading state while restoring the session", async () => {
    useAuthMock.mockReturnValue(auth({ loading: true }));
    await renderShell();
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("renders the login route bare (no header) and does not redirect", async () => {
    pathnameMock.mockReturnValue("/login");
    useAuthMock.mockReturnValue(auth({ principal: null }));
    await renderShell();
    expect(screen.getByTestId("page")).toBeInTheDocument();
    expect(screen.queryByTestId("app-header")).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("sends an authenticated user away from /login to home", async () => {
    pathnameMock.mockReturnValue("/login");
    useAuthMock.mockReturnValue(auth({ principal: USER }));
    await renderShell();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/"));
  });
});
