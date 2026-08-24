import type { AuthContextValue } from "@/components/AuthProvider";
import { CurrentUserProvider, useCurrentUser } from "@/components/CurrentUserProvider";
import type { Principal } from "@/lib/api-types";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getEmployeesMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getEmployees: () => getEmployeesMock(),
}));

const useAuthMock = vi.fn<() => AuthContextValue>();
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => useAuthMock(),
}));

const EMPLOYEES = [
  { id: "E001", name: "山田 太郎", dept: "営業部" },
  { id: "E002", name: "佐藤 花子", dept: "技術部" },
];

const ADMIN: Principal = { id: null, name: "管理者", dept: null, is_admin: true };
const USER: Principal = { id: "E007", name: "田中 一郎", dept: "第2営業部", is_admin: false };

const STORAGE_KEY = "tekijin.currentUserId";

function setPrincipal(principal: Principal | null): void {
  useAuthMock.mockReturnValue({
    principal,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  });
}

function Consumer() {
  const {
    currentUserId,
    currentUser,
    employees,
    loading,
    error,
    reload,
    setCurrentUserId,
    canSwitch,
  } = useCurrentUser();
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="error">{String(error)}</span>
      <span data-testid="count">{employees.length}</span>
      <span data-testid="current">{currentUserId ?? "none"}</span>
      <span data-testid="name">{currentUser?.name ?? "none"}</span>
      <span data-testid="canSwitch">{String(canSwitch)}</span>
      <button type="button" onClick={() => setCurrentUserId("E002")}>
        switch
      </button>
      <button type="button" onClick={reload}>
        reload
      </button>
    </div>
  );
}

function renderProvider() {
  return render(
    <CurrentUserProvider>
      <Consumer />
    </CurrentUserProvider>,
  );
}

beforeEach(() => {
  getEmployeesMock.mockReset();
  window.localStorage.clear();
  setPrincipal(ADMIN); // most tests exercise the admin switcher
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CurrentUserProvider — admin (demo switcher)", () => {
  it("loads the directory and defaults to the first employee", async () => {
    getEmployeesMock.mockResolvedValue(EMPLOYEES);
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E001"));
    expect(screen.getByTestId("count")).toHaveTextContent("2");
    expect(screen.getByTestId("name")).toHaveTextContent("山田 太郎");
    expect(screen.getByTestId("canSwitch")).toHaveTextContent("true");
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });

  it("restores a remembered user when it still exists", async () => {
    window.localStorage.setItem(STORAGE_KEY, "E002");
    getEmployeesMock.mockResolvedValue(EMPLOYEES);
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E002"));
    expect(screen.getByTestId("name")).toHaveTextContent("佐藤 花子");
  });

  it("ignores a stale stored user and falls back to the first employee", async () => {
    window.localStorage.setItem(STORAGE_KEY, "E999");
    getEmployeesMock.mockResolvedValue(EMPLOYEES);
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E001"));
  });

  it("persists a new selection to localStorage", async () => {
    getEmployeesMock.mockResolvedValue(EMPLOYEES);
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E001"));

    act(() => {
      screen.getByRole("button", { name: "switch" }).click();
    });

    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E002"));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("E002");
  });

  it("leaves the directory empty and the user null on load failure", async () => {
    getEmployeesMock.mockRejectedValue(new Error("network"));
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("count")).toHaveTextContent("0");
    expect(screen.getByTestId("current")).toHaveTextContent("none");
    expect(screen.getByTestId("error")).toHaveTextContent("true");
  });

  it("recovers when reload() is called after a failure (#179)", async () => {
    getEmployeesMock.mockRejectedValueOnce(new Error("network"));
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent("true"));

    getEmployeesMock.mockResolvedValueOnce(EMPLOYEES);
    act(() => {
      screen.getByRole("button", { name: "reload" }).click();
    });

    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E001"));
    expect(screen.getByTestId("error")).toHaveTextContent("false");
    expect(screen.getByTestId("count")).toHaveTextContent("2");
  });
});

describe("CurrentUserProvider — regular user (self only)", () => {
  it("acts as the logged-in user with no directory and no switching", async () => {
    setPrincipal(USER);
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E007"));
    expect(screen.getByTestId("name")).toHaveTextContent("田中 一郎");
    expect(screen.getByTestId("count")).toHaveTextContent("0"); // no directory
    expect(screen.getByTestId("canSwitch")).toHaveTextContent("false");
    expect(getEmployeesMock).not.toHaveBeenCalled(); // never fetches /employees
  });

  it("ignores setCurrentUserId (a regular user cannot switch)", async () => {
    setPrincipal(USER);
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E007"));

    act(() => {
      screen.getByRole("button", { name: "switch" }).click();
    });
    // Still acting as themselves.
    expect(screen.getByTestId("current")).toHaveTextContent("E007");
  });
});

describe("CurrentUserProvider — inert default", () => {
  it("provides an inert default outside a provider", () => {
    render(<Consumer />);
    expect(screen.getByTestId("current")).toHaveTextContent("none");
    expect(screen.getByTestId("count")).toHaveTextContent("0");
    act(() => {
      screen.getByRole("button", { name: "switch" }).click();
    });
    expect(screen.getByTestId("current")).toHaveTextContent("none");
  });
});
