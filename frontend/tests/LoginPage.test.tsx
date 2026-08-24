import LoginPage from "@/../app/login/page";
import type { AuthContextValue } from "@/components/AuthProvider";
import { ApiError } from "@/lib/api-client";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useAuthMock = vi.fn<() => AuthContextValue>();
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => useAuthMock(),
}));

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

function auth(login: AuthContextValue["login"]): AuthContextValue {
  return { principal: null, loading: false, login, logout: vi.fn() };
}

beforeEach(() => {
  replaceMock.mockReset();
});

afterEach(() => {
  useAuthMock.mockReset();
});

function fill(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("メールアドレス"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("パスワード"), { target: { value: password } });
}

describe("LoginPage", () => {
  it("logs in and redirects home on success", async () => {
    const login = vi.fn().mockResolvedValue(undefined);
    useAuthMock.mockReturnValue(auth(login));
    render(<LoginPage />);
    fill("a@x.com", "pw");
    fireEvent.click(screen.getByRole("button", { name: "ログイン" }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("a@x.com", "pw"));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/"));
  });

  it("shows a credentials error on 401", async () => {
    const login = vi.fn().mockRejectedValue(new ApiError(401, "bad"));
    useAuthMock.mockReturnValue(auth(login));
    render(<LoginPage />);
    fill("a@x.com", "wrong");
    fireEvent.click(screen.getByRole("button", { name: "ログイン" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "メールアドレスまたはパスワードが違います。",
    );
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("shows a rate-limit error on 429", async () => {
    const login = vi.fn().mockRejectedValue(new ApiError(429, "slow down"));
    useAuthMock.mockReturnValue(auth(login));
    render(<LoginPage />);
    fill("a@x.com", "pw");
    fireEvent.click(screen.getByRole("button", { name: "ログイン" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ログイン試行が多すぎます");
  });

  it("disables submit until both fields are filled", () => {
    useAuthMock.mockReturnValue(auth(vi.fn()));
    render(<LoginPage />);
    const button = screen.getByRole("button", { name: "ログイン" });
    expect(button).toBeDisabled();
    fill("a@x.com", "pw");
    expect(button).toBeEnabled();
  });
});
