import { AppHeader } from "@/components/AppHeader";
import type { AuthContextValue } from "@/components/AuthProvider";
import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import type { Principal } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const useAuthMock = vi.fn<() => AuthContextValue>();
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => useAuthMock(),
}));

const pathnameMock = vi.fn<() => string>();
const pushMock = vi.fn();
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => pathnameMock(),
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
}));

const getNotificationsMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getNotifications: (...args: unknown[]) => getNotificationsMock(...args),
  ackNotifications: vi.fn(),
}));

const EMPLOYEES = [
  { id: "E001", name: "山田 太郎", dept: "営業部" },
  { id: "E002", name: "佐藤 花子", dept: "技術部" },
];

const ADMIN: Principal = { id: null, name: "管理者", dept: null, is_admin: true };
const USER: Principal = { id: "E001", name: "山田 太郎", dept: "営業部", is_admin: false };

function auth(principal: Principal): AuthContextValue {
  return { principal, loading: false, login: vi.fn(), logout: vi.fn() };
}

function ctx(partial: Partial<CurrentUserContextValue>): CurrentUserContextValue {
  return {
    employees: [],
    currentUserId: null,
    currentUser: null,
    setCurrentUserId: vi.fn(),
    loading: false,
    error: false,
    reload: vi.fn(),
    canSwitch: false,
    ...partial,
  };
}

const ADMIN_READY = ctx({
  employees: EMPLOYEES,
  currentUserId: "E001",
  currentUser: EMPLOYEES[0],
  canSwitch: true,
});

beforeEach(() => {
  pathnameMock.mockReturnValue("/");
  useAuthMock.mockReturnValue(auth(ADMIN));
  getNotificationsMock.mockReset();
  getNotificationsMock.mockResolvedValue([]);
});

afterEach(() => {
  useCurrentUserMock.mockReset();
  useAuthMock.mockReset();
  pathnameMock.mockReset();
  pushMock.mockReset();
  replaceMock.mockReset();
});

describe("AppHeader", () => {
  it("renders the brand logo", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    render(<AppHeader />);
    const logo = screen.getByAltText("TEKIJIN");
    expect(logo).toBeInTheDocument();
    expect(logo).toHaveAttribute("src", "/tekijin-logo.png");
    // The retired "たずねーる" subtitle must no longer render (#196).
    expect(screen.queryByText("たずねーる")).not.toBeInTheDocument();
  });

  // --- admin: demo switcher --------------------------------------------------- #
  it("populates the switcher from the directory and reflects the current user (admin)", () => {
    useCurrentUserMock.mockReturnValue(
      ctx({
        employees: EMPLOYEES,
        currentUserId: "E002",
        currentUser: EMPLOYEES[1],
        canSwitch: true,
      }),
    );
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ }) as HTMLSelectElement;
    expect(select).toBeEnabled();
    expect(select.querySelectorAll("option")).toHaveLength(2);
    expect(select.value).toBe("E002");
    expect(screen.getByText("山田 太郎（営業部）")).toBeInTheDocument();
  });

  it("calls setCurrentUserId and navigates home when a different user is selected (admin, #210)", () => {
    const setCurrentUserId = vi.fn();
    useCurrentUserMock.mockReturnValue(
      ctx({
        employees: EMPLOYEES,
        currentUserId: "E001",
        currentUser: EMPLOYEES[0],
        setCurrentUserId,
        canSwitch: true,
      }),
    );
    pathnameMock.mockReturnValue("/inbox");
    render(<AppHeader />);
    fireEvent.change(screen.getByRole("combobox", { name: /利用者を切替/ }), {
      target: { value: "E002" },
    });
    expect(setCurrentUserId).toHaveBeenCalledWith("E002");
    expect(pushMock).toHaveBeenCalledWith("/");
  });

  it("shows a disabled placeholder while the directory is loading (admin)", () => {
    useCurrentUserMock.mockReturnValue(ctx({ loading: true, canSwitch: true }));
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ });
    expect(select).toBeDisabled();
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
  });

  it("surfaces an inline retry when the directory load failed (admin, #179)", () => {
    const reload = vi.fn();
    useCurrentUserMock.mockReturnValue(ctx({ error: true, reload, canSwitch: true }));
    render(<AppHeader />);
    fireEvent.click(screen.getByRole("button", { name: /取得に失敗しました。再試行/ }));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("shows the dashboard link for an admin", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    render(<AppHeader />);
    const nav = screen.getByRole("navigation", { name: "メインナビゲーション" });
    expect(within(nav).getByRole("link", { name: "ダッシュボード" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  // --- regular user: no switcher, no dashboard -------------------------------- #
  it("hides the switcher and dashboard for a regular user, showing their name", () => {
    useAuthMock.mockReturnValue(auth(USER));
    useCurrentUserMock.mockReturnValue(
      ctx({ currentUserId: "E001", currentUser: EMPLOYEES[0], canSwitch: false }),
    );
    render(<AppHeader />);
    expect(screen.queryByRole("combobox", { name: /利用者を切替/ })).not.toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "メインナビゲーション" });
    expect(within(nav).queryByRole("link", { name: "ダッシュボード" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "質問する" })).toBeInTheDocument();
    // 質問履歴 (#208) is available to everyone, not admin-gated.
    expect(within(nav).getByRole("link", { name: "質問履歴" })).toHaveAttribute("href", "/history");
    expect(screen.getByText("山田 太郎")).toBeInTheDocument();
  });

  it("logs out and returns to /login when the logout button is clicked", async () => {
    const logout = vi.fn().mockResolvedValue(undefined);
    useAuthMock.mockReturnValue({ ...auth(USER), logout });
    useCurrentUserMock.mockReturnValue(
      ctx({ currentUserId: "E001", currentUser: EMPLOYEES[0], canSwitch: false }),
    );
    render(<AppHeader />);
    fireEvent.click(screen.getByRole("button", { name: "ログアウト" }));
    expect(logout).toHaveBeenCalledTimes(1);
    // replace("/login") happens after the async logout resolves.
    await vi.waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  // --- navigation active state ------------------------------------------------ #
  it("marks the current section with aria-current", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    pathnameMock.mockReturnValue("/inbox");
    render(<AppHeader />);
    expect(screen.getByRole("link", { name: "受信箱" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "質問する" })).not.toHaveAttribute("aria-current");
  });

  it("treats a nested route as active for its section", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    pathnameMock.mockReturnValue("/dashboard/detail");
    render(<AppHeader />);
    expect(screen.getByRole("link", { name: "ダッシュボード" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("shows the decline-notification bell for the acting user (#225)", async () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    render(<AppHeader />);
    await waitFor(() => expect(getNotificationsMock).toHaveBeenCalledWith("E001"));
    expect(screen.getByRole("button", { name: "通知" })).toBeInTheDocument();
  });

  it("does not show the notification bell before a current user is resolved", () => {
    useCurrentUserMock.mockReturnValue(ctx({ loading: true }));
    render(<AppHeader />);
    expect(screen.queryByRole("button", { name: /通知/ })).not.toBeInTheDocument();
  });

  it("does not mount the bell at all when logged out (#241: nobody to poll for)", () => {
    useAuthMock.mockReturnValue(auth(null as unknown as Principal));
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    render(<AppHeader />);
    expect(screen.queryByRole("button", { name: /通知/ })).not.toBeInTheDocument();
    expect(getNotificationsMock).not.toHaveBeenCalled();
  });
});
