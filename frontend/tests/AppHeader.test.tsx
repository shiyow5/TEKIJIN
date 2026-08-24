import { AppHeader } from "@/components/AppHeader";
import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const pathnameMock = vi.fn<() => string>();
vi.mock("next/navigation", () => ({
  usePathname: () => pathnameMock(),
}));

const EMPLOYEES = [
  { id: "E001", name: "山田 太郎", dept: "営業部" },
  { id: "E002", name: "佐藤 花子", dept: "技術部" },
];

const READY: CurrentUserContextValue = {
  employees: EMPLOYEES,
  currentUserId: "E001",
  currentUser: EMPLOYEES[0],
  setCurrentUserId: () => {},
  loading: false,
  error: false,
  reload: () => {},
};

beforeEach(() => {
  pathnameMock.mockReturnValue("/");
});

afterEach(() => {
  useCurrentUserMock.mockReset();
  pathnameMock.mockReset();
});

describe("AppHeader", () => {
  it("renders the brand logo", () => {
    useCurrentUserMock.mockReturnValue({
      employees: EMPLOYEES,
      currentUserId: "E001",
      currentUser: EMPLOYEES[0],
      setCurrentUserId: vi.fn(),
      loading: false,
      error: false,
      reload: () => {},
    });
    render(<AppHeader />);
    // The brand is now a logo image (#201); its alt carries the product name.
    const logo = screen.getByAltText("TEKIJIN");
    expect(logo).toBeInTheDocument();
    expect(logo).toHaveAttribute("src", "/tekijin-logo.jpg");
    // The retired "たずねーる" subtitle must no longer render (#196).
    expect(screen.queryByText("たずねーる")).not.toBeInTheDocument();
  });

  it("populates the switcher from the directory and reflects the current user", () => {
    useCurrentUserMock.mockReturnValue({
      employees: EMPLOYEES,
      currentUserId: "E002",
      currentUser: EMPLOYEES[1],
      setCurrentUserId: vi.fn(),
      loading: false,
      error: false,
      reload: () => {},
    });
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ }) as HTMLSelectElement;
    expect(select).toBeEnabled();
    expect(select.querySelectorAll("option")).toHaveLength(2);
    expect(select.value).toBe("E002");
    expect(screen.getByText("山田 太郎（営業部）")).toBeInTheDocument();
  });

  it("calls setCurrentUserId when a different user is selected", () => {
    const setCurrentUserId = vi.fn();
    useCurrentUserMock.mockReturnValue({
      employees: EMPLOYEES,
      currentUserId: "E001",
      currentUser: EMPLOYEES[0],
      setCurrentUserId,
      loading: false,
      error: false,
      reload: () => {},
    });
    render(<AppHeader />);
    fireEvent.change(screen.getByRole("combobox", { name: /利用者を切替/ }), {
      target: { value: "E002" },
    });
    expect(setCurrentUserId).toHaveBeenCalledWith("E002");
  });

  it("shows a disabled placeholder while the directory is loading", () => {
    useCurrentUserMock.mockReturnValue({
      employees: [],
      currentUserId: null,
      currentUser: null,
      setCurrentUserId: vi.fn(),
      loading: true,
      error: false,
      reload: () => {},
    });
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: /利用者を切替/ });
    expect(select).toBeDisabled();
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
  });

  it("shows an unavailable placeholder when the directory failed to load", () => {
    useCurrentUserMock.mockReturnValue({
      employees: [],
      currentUserId: null,
      currentUser: null,
      setCurrentUserId: vi.fn(),
      loading: false,
      error: false,
      reload: () => {},
    });
    render(<AppHeader />);
    expect(screen.getByRole("combobox", { name: /利用者を切替/ })).toBeDisabled();
    expect(screen.getByText("利用できません")).toBeInTheDocument();
  });

  it("surfaces an inline retry when the directory load failed and calls reload (#179)", () => {
    const reload = vi.fn();
    useCurrentUserMock.mockReturnValue({
      employees: [],
      currentUserId: null,
      currentUser: null,
      setCurrentUserId: vi.fn(),
      loading: false,
      error: true,
      reload,
    });
    render(<AppHeader />);
    const retry = screen.getByRole("button", { name: /取得に失敗しました。再試行/ });
    fireEvent.click(retry);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("renders global navigation with a home link and the main destinations", () => {
    useCurrentUserMock.mockReturnValue(READY);
    render(<AppHeader />);
    // The brand links home.
    expect(screen.getByRole("link", { name: /TEKIJIN/ })).toHaveAttribute("href", "/");
    const nav = screen.getByRole("navigation", { name: "メインナビゲーション" });
    expect(within(nav).getByRole("link", { name: "質問する" })).toHaveAttribute(
      "href",
      "/questions",
    );
    expect(within(nav).getByRole("link", { name: "受信箱" })).toHaveAttribute("href", "/inbox");
    expect(within(nav).getByRole("link", { name: "ダッシュボード" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("marks the current section with aria-current", () => {
    useCurrentUserMock.mockReturnValue(READY);
    pathnameMock.mockReturnValue("/inbox");
    render(<AppHeader />);
    expect(screen.getByRole("link", { name: "受信箱" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "質問する" })).not.toHaveAttribute("aria-current");
  });

  it("treats a nested route as active for its section", () => {
    useCurrentUserMock.mockReturnValue(READY);
    // A child route (e.g. a future /dashboard/detail) keeps its section marked.
    pathnameMock.mockReturnValue("/dashboard/detail");
    render(<AppHeader />);
    expect(screen.getByRole("link", { name: "ダッシュボード" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
