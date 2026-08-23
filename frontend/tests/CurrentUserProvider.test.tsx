import { CurrentUserProvider, useCurrentUser } from "@/components/CurrentUserProvider";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getEmployeesMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getEmployees: () => getEmployeesMock(),
}));

const EMPLOYEES = [
  { id: "E001", name: "山田 太郎", dept: "営業部" },
  { id: "E002", name: "佐藤 花子", dept: "技術部" },
];

const STORAGE_KEY = "tekijin.currentUserId";

function Consumer() {
  const { currentUserId, currentUser, employees, loading, setCurrentUserId } = useCurrentUser();
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="count">{employees.length}</span>
      <span data-testid="current">{currentUserId ?? "none"}</span>
      <span data-testid="name">{currentUser?.name ?? "none"}</span>
      <button type="button" onClick={() => setCurrentUserId("E002")}>
        switch
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
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CurrentUserProvider", () => {
  it("loads the directory and defaults to the first employee", async () => {
    getEmployeesMock.mockResolvedValue(EMPLOYEES);
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("E001"));
    expect(screen.getByTestId("count")).toHaveTextContent("2");
    expect(screen.getByTestId("name")).toHaveTextContent("山田 太郎");
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
  });

  it("provides an inert default outside a provider", () => {
    render(<Consumer />);
    expect(screen.getByTestId("current")).toHaveTextContent("none");
    expect(screen.getByTestId("count")).toHaveTextContent("0");
    // The inert setter is a no-op and must not throw.
    act(() => {
      screen.getByRole("button", { name: "switch" }).click();
    });
    expect(screen.getByTestId("current")).toHaveTextContent("none");
  });
});
