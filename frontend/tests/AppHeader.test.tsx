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
  return { principal, loading: false, login: vi.fn(), logout: vi.fn(), adoptToken: vi.fn() };
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

function openMenu() {
  fireEvent.click(screen.getByRole("button", { name: "メニューを開く" }));
  return document.getElementById("nav-menu") as HTMLElement;
}

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
  function switcher(partial: Partial<CurrentUserContextValue>): CurrentUserContextValue {
    return ctx({
      employees: EMPLOYEES,
      currentUserId: "E001",
      currentUser: EMPLOYEES[0],
      canSwitch: true,
      ...partial,
    });
  }

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

  it("does not switch or navigate while the select is only being browsed (#231)", () => {
    // A native <select> fires `change` for every arrow key with the popup closed —
    // it does not wait for Enter. Switching there meant walking the list threw the
    // admin home on each keypress, discarding an unsent draft they never left.
    const setCurrentUserId = vi.fn();
    useCurrentUserMock.mockReturnValue(switcher({ setCurrentUserId }));
    pathnameMock.mockReturnValue("/answer/s1");
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ }) as HTMLSelectElement;

    fireEvent.change(select, { target: { value: "E002" } });

    expect(setCurrentUserId).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
    // The browsed value is still SHOWN — the user must see what they are pointing at.
    expect(select.value).toBe("E002");
  });

  it("switches and navigates home only when 切替 is pressed (#210, #231)", () => {
    const setCurrentUserId = vi.fn();
    useCurrentUserMock.mockReturnValue(switcher({ setCurrentUserId }));
    pathnameMock.mockReturnValue("/inbox");
    render(<AppHeader />);

    fireEvent.change(screen.getByRole("combobox", { name: /利用者を切替/ }), {
      target: { value: "E002" },
    });
    fireEvent.click(screen.getByRole("button", { name: "切替" }));

    expect(setCurrentUserId).toHaveBeenCalledWith("E002");
    expect(pushMock).toHaveBeenCalledWith("/");
  });

  it("applies on Enter so a keyboard user need not reach for the button (#231)", () => {
    const setCurrentUserId = vi.fn();
    useCurrentUserMock.mockReturnValue(switcher({ setCurrentUserId }));
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ });

    fireEvent.change(select, { target: { value: "E002" } });
    fireEvent.keyDown(select, { key: "Enter" });

    expect(setCurrentUserId).toHaveBeenCalledWith("E002");
    expect(pushMock).toHaveBeenCalledWith("/");
  });

  it("does not switch when focus simply leaves the select (#231)", () => {
    // The whole point: clicking back into your own textarea after peeking at the
    // list must NOT switch identity and throw you home. Committing on blur would
    // reintroduce exactly the draft loss this issue is about.
    const setCurrentUserId = vi.fn();
    useCurrentUserMock.mockReturnValue(switcher({ setCurrentUserId }));
    pathnameMock.mockReturnValue("/answer/s1");
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ });

    fireEvent.change(select, { target: { value: "E002" } });
    fireEvent.blur(select);

    expect(setCurrentUserId).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("keeps 切替 disabled until the selection differs from the current user (#231)", () => {
    useCurrentUserMock.mockReturnValue(switcher({}));
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ });
    expect(screen.getByRole("button", { name: "切替" })).toBeDisabled();

    fireEvent.change(select, { target: { value: "E002" } });
    expect(screen.getByRole("button", { name: "切替" })).toBeEnabled();

    fireEvent.change(select, { target: { value: "E001" } });
    expect(screen.getByRole("button", { name: "切替" })).toBeDisabled();
  });

  it("does not switch when the browsing ends back on the current user (#231)", () => {
    const setCurrentUserId = vi.fn();
    useCurrentUserMock.mockReturnValue(switcher({ setCurrentUserId }));
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ });

    fireEvent.change(select, { target: { value: "E002" } });
    fireEvent.change(select, { target: { value: "E001" } });
    fireEvent.keyDown(select, { key: "Enter" });

    expect(setCurrentUserId).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("drops a stale selection when the acting user changes from elsewhere (#231)", () => {
    // A reload restoring the default, or another tab. The pending value must not
    // outlive it: the header would show E002 while the app acts as E003, and the
    // next apply would silently revert a change the admin never made.
    const THIRD = { id: "E003", name: "鈴木 次郎", dept: "総務部" };
    const all = [...EMPLOYEES, THIRD];
    useCurrentUserMock.mockReturnValue(switcher({ employees: all }));
    const { rerender } = render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ }) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "E002" } });
    expect(select.value).toBe("E002");

    useCurrentUserMock.mockReturnValue(
      switcher({ employees: all, currentUserId: "E003", currentUser: THIRD }),
    );
    rerender(<AppHeader />);

    expect(
      (screen.getByRole("combobox", { name: /利用者を切替/ }) as HTMLSelectElement).value,
    ).toBe("E003");
    expect(screen.getByRole("button", { name: "切替" })).toBeDisabled();
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

  it("shows the dashboard link for an admin, and not for a regular user", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    const { rerender } = render(<AppHeader />);
    let nav = within(openMenu()).getByRole("navigation", { name: "メインナビゲーション" });
    expect(within(nav).getByRole("link", { name: "ダッシュボード" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(within(nav).getByRole("link", { name: "チャット" })).toHaveAttribute("href", "/chat");
    expect(within(nav).getByRole("link", { name: "ナレッジ" })).toHaveAttribute(
      "href",
      "/knowledge",
    );

    useAuthMock.mockReturnValue(auth(USER));
    useCurrentUserMock.mockReturnValue(
      ctx({ currentUserId: "E001", currentUser: EMPLOYEES[0], canSwitch: false }),
    );
    // Rerenders the same instance — the menu is already open from above.
    rerender(<AppHeader />);
    nav = within(document.getElementById("nav-menu") as HTMLElement).getByRole("navigation", {
      name: "メインナビゲーション",
    });
    expect(within(nav).queryByRole("link", { name: "ダッシュボード" })).not.toBeInTheDocument();
  });

  it("shows a トップ link back to the hub, right after the name", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    pathnameMock.mockReturnValue("/inbox");
    render(<AppHeader />);
    const menu = openMenu();
    const nav = within(menu).getByRole("navigation", { name: "メインナビゲーション" });
    expect(within(nav).getByRole("link", { name: "トップ" })).toHaveAttribute("href", "/");

    const labels = within(menu)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(labels[0]).toBe("トップ");
  });

  // "質問する" is folded into the top page's own question bar (#391), not the nav.
  it("never shows a 質問する nav link", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    render(<AppHeader />);
    const nav = within(openMenu()).getByRole("navigation", { name: "メインナビゲーション" });
    expect(within(nav).queryByRole("link", { name: "質問する" })).not.toBeInTheDocument();
  });

  // --- regular user: no switcher, no dashboard -------------------------------- #
  it("hides the switcher and dashboard for a regular user, showing their name in the menu", () => {
    useAuthMock.mockReturnValue(auth(USER));
    useCurrentUserMock.mockReturnValue(
      ctx({ currentUserId: "E001", currentUser: EMPLOYEES[0], canSwitch: false }),
    );
    render(<AppHeader />);
    expect(screen.queryByRole("combobox", { name: /利用者を切替/ })).not.toBeInTheDocument();
    const menu = openMenu();
    const nav = within(menu).getByRole("navigation", { name: "メインナビゲーション" });
    expect(within(nav).queryByRole("link", { name: "ダッシュボード" })).not.toBeInTheDocument();
    // 質問履歴 (#208) is available to everyone, not admin-gated.
    expect(within(nav).getByRole("link", { name: "質問履歴" })).toHaveAttribute("href", "/history");
    expect(within(menu).getByText("山田 太郎")).toBeInTheDocument();
  });

  it("shows the acting admin's own name in the menu, distinct from the demo switcher", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    render(<AppHeader />);
    const menu = openMenu();
    expect(within(menu).getByText("管理者（管理者）")).toBeInTheDocument();
  });

  it("logs out and returns to /login when the logout button is clicked", async () => {
    const logout = vi.fn().mockResolvedValue(undefined);
    useAuthMock.mockReturnValue({ ...auth(USER), logout });
    useCurrentUserMock.mockReturnValue(
      ctx({ currentUserId: "E001", currentUser: EMPLOYEES[0], canSwitch: false }),
    );
    render(<AppHeader />);
    const menu = openMenu();
    fireEvent.click(within(menu).getByRole("button", { name: "ログアウト" }));
    expect(logout).toHaveBeenCalledTimes(1);
    // replace("/login") happens after the async logout resolves.
    await vi.waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });

  // --- navigation active state ------------------------------------------------ #
  it("marks the current section with aria-current", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    pathnameMock.mockReturnValue("/inbox");
    render(<AppHeader />);
    const menu = openMenu();
    expect(within(menu).getByRole("link", { name: "受信箱" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(menu).getByRole("link", { name: "チャット" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("treats a nested route as active for its section", () => {
    useCurrentUserMock.mockReturnValue(ADMIN_READY);
    pathnameMock.mockReturnValue("/dashboard/detail");
    render(<AppHeader />);
    const menu = openMenu();
    expect(within(menu).getByRole("link", { name: "ダッシュボード" })).toHaveAttribute(
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

  describe("nav menu (#254, unified to a single hamburger at every width by #391)", () => {
    it("starts collapsed and opens on click, exposing the same destinations", () => {
      useCurrentUserMock.mockReturnValue(ADMIN_READY);
      render(<AppHeader />);
      const toggle = screen.getByRole("button", { name: "メニューを開く" });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("link", { name: "ダッシュボード" })).not.toBeInTheDocument();

      fireEvent.click(toggle);

      expect(screen.getByRole("button", { name: "メニューを閉じる" })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
      const navMenu = document.getElementById("nav-menu");
      expect(navMenu).not.toBeNull();
      expect(within(navMenu as HTMLElement).getByRole("link", { name: "受信箱" })).toHaveAttribute(
        "href",
        "/inbox",
      );
    });

    it("closes when a destination in the menu is clicked", () => {
      useCurrentUserMock.mockReturnValue(ADMIN_READY);
      render(<AppHeader />);
      const navMenu = openMenu();
      fireEvent.click(within(navMenu).getByRole("link", { name: "受信箱" }));
      expect(document.getElementById("nav-menu")).toBeNull();
      expect(screen.getByRole("button", { name: "メニューを開く" })).toHaveAttribute(
        "aria-expanded",
        "false",
      );
    });

    it("closes on Escape and returns focus to the toggle button", () => {
      useCurrentUserMock.mockReturnValue(ADMIN_READY);
      render(<AppHeader />);
      openMenu();
      expect(document.getElementById("nav-menu")).not.toBeNull();

      fireEvent.keyDown(document, { key: "Escape" });

      expect(document.getElementById("nav-menu")).toBeNull();
      expect(screen.getByRole("button", { name: "メニューを開く" })).toHaveFocus();
    });

    it("closes when clicking outside the menu", () => {
      useCurrentUserMock.mockReturnValue(ADMIN_READY);
      render(<AppHeader />);
      openMenu();
      expect(document.getElementById("nav-menu")).not.toBeNull();

      fireEvent.mouseDown(document.body);

      expect(document.getElementById("nav-menu")).toBeNull();
    });

    it("closes when the route changes (e.g. after switching users)", () => {
      useCurrentUserMock.mockReturnValue(ADMIN_READY);
      const { rerender } = render(<AppHeader />);
      openMenu();
      expect(document.getElementById("nav-menu")).not.toBeNull();

      pathnameMock.mockReturnValue("/inbox");
      rerender(<AppHeader />);

      expect(document.getElementById("nav-menu")).toBeNull();
    });

    it("keeps logout reachable from the menu, as the only logout control (#288, #391)", async () => {
      const logout = vi.fn().mockResolvedValue(undefined);
      useAuthMock.mockReturnValue({ ...auth(USER), logout });
      useCurrentUserMock.mockReturnValue(ADMIN_READY);
      render(<AppHeader />);

      expect(screen.queryByRole("button", { name: "ログアウト" })).not.toBeInTheDocument();
      const navMenu = openMenu();
      fireEvent.click(within(navMenu).getByRole("button", { name: "ログアウト" }));

      expect(logout).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    });
  });
});
