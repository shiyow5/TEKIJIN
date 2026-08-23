import { AppHeader } from "@/components/AppHeader";
import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const EMPLOYEES = [
  { id: "E001", name: "山田 太郎", dept: "営業部" },
  { id: "E002", name: "佐藤 花子", dept: "技術部" },
];

afterEach(() => {
  useCurrentUserMock.mockReset();
});

describe("AppHeader", () => {
  it("renders the product name", () => {
    useCurrentUserMock.mockReturnValue({
      employees: EMPLOYEES,
      currentUserId: "E001",
      currentUser: EMPLOYEES[0],
      setCurrentUserId: vi.fn(),
      loading: false,
    });
    render(<AppHeader />);
    expect(screen.getByText("TEKIJIN")).toBeInTheDocument();
    expect(screen.getByText("たずねーる")).toBeInTheDocument();
  });

  it("populates the switcher from the directory and reflects the current user", () => {
    useCurrentUserMock.mockReturnValue({
      employees: EMPLOYEES,
      currentUserId: "E002",
      currentUser: EMPLOYEES[1],
      setCurrentUserId: vi.fn(),
      loading: false,
    });
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: "ユーザー切替" }) as HTMLSelectElement;
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
    });
    render(<AppHeader />);
    fireEvent.change(screen.getByRole("combobox", { name: "ユーザー切替" }), {
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
    });
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: "ユーザー切替" });
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
    });
    render(<AppHeader />);
    expect(screen.getByRole("combobox", { name: "ユーザー切替" })).toBeDisabled();
    expect(screen.getByText("利用できません")).toBeInTheDocument();
  });
});
