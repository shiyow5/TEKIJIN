import { AuthProvider, useAuth } from "@/components/AuthProvider";
import type { Principal } from "@/lib/api-types";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getMeMock = vi.fn();
const postLoginMock = vi.fn();
const postLogoutMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getMe: () => getMeMock(),
  postLogin: (body: unknown) => postLoginMock(body),
  postLogout: () => postLogoutMock(),
}));

const loadStoredTokenMock = vi.fn();
const setAuthTokenMock = vi.fn();
vi.mock("@/lib/auth-token", () => ({
  loadStoredToken: () => loadStoredTokenMock(),
  setAuthToken: (t: string | null) => setAuthTokenMock(t),
}));

const USER: Principal = { id: "E005", name: "山田", dept: "営業", is_admin: false };

function Consumer() {
  const { principal, loading, login, logout } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="who">{principal ? principal.name : "none"}</span>
      <button type="button" onClick={() => login("a@x", "pw")}>
        login
      </button>
      <button type="button" onClick={() => logout()}>
        logout
      </button>
    </div>
  );
}

function renderProvider() {
  return render(
    <AuthProvider>
      <Consumer />
    </AuthProvider>,
  );
}

beforeEach(() => {
  getMeMock.mockReset();
  postLoginMock.mockReset();
  postLogoutMock.mockReset().mockResolvedValue(undefined);
  loadStoredTokenMock.mockReset();
  setAuthTokenMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AuthProvider", () => {
  it("stays logged out when there is no stored token", async () => {
    loadStoredTokenMock.mockReturnValue(null);
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("who")).toHaveTextContent("none");
    expect(getMeMock).not.toHaveBeenCalled();
  });

  it("restores the session from a stored token via /auth/me", async () => {
    loadStoredTokenMock.mockReturnValue("stored-token");
    getMeMock.mockResolvedValue(USER);
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("山田"));
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });

  it("drops an invalid stored token and stays logged out", async () => {
    loadStoredTokenMock.mockReturnValue("expired");
    getMeMock.mockRejectedValue(new Error("401"));
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("who")).toHaveTextContent("none");
    expect(setAuthTokenMock).toHaveBeenCalledWith(null);
  });

  it("login stores the token and sets the principal", async () => {
    loadStoredTokenMock.mockReturnValue(null);
    postLoginMock.mockResolvedValue({ access_token: "new-token", principal: USER });
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    await act(async () => {
      screen.getByRole("button", { name: "login" }).click();
    });
    expect(postLoginMock).toHaveBeenCalledWith({ email: "a@x", password: "pw" });
    expect(setAuthTokenMock).toHaveBeenCalledWith("new-token");
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("山田"));
  });

  it("logout clears the token and the principal", async () => {
    loadStoredTokenMock.mockReturnValue("stored-token");
    getMeMock.mockResolvedValue(USER);
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("山田"));

    await act(async () => {
      screen.getByRole("button", { name: "logout" }).click();
    });
    expect(postLogoutMock).toHaveBeenCalledTimes(1);
    expect(setAuthTokenMock).toHaveBeenCalledWith(null);
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("none"));
  });

  it("exposes an inert default outside a provider", () => {
    render(<Consumer />);
    expect(screen.getByTestId("who")).toHaveTextContent("none");
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });
});
