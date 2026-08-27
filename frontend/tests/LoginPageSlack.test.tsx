import LoginPage from "@/../app/login/page";
import type { AuthContextValue } from "@/components/AuthProvider";
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

const getSlackLoginUrlMock = vi.fn();
vi.mock("@/lib/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api-client")>()),
  getSlackLoginUrl: () => getSlackLoginUrlMock(),
}));

function auth(over: Partial<AuthContextValue> = {}): AuthContextValue {
  return {
    principal: null,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    adoptToken: vi.fn().mockResolvedValue(undefined),
    ...over,
  };
}

function setUrl(search: string, hash: string) {
  window.history.replaceState({}, "", `/login${search}${hash}`);
}

beforeEach(() => {
  replaceMock.mockReset();
  getSlackLoginUrlMock.mockReset();
  getSlackLoginUrlMock.mockResolvedValue({ url: "https://slack.com/oauth/v2/authorize?x=1" });
  setUrl("", "");
});

afterEach(() => {
  useAuthMock.mockReset();
});

describe("LoginPage — Slack login (#406)", () => {
  it("adopts a token handed back in the URL fragment and goes home", async () => {
    const adoptToken = vi.fn().mockResolvedValue(undefined);
    useAuthMock.mockReturnValue(auth({ adoptToken }));
    setUrl("", "#slack_token=jwt-abc");

    render(<LoginPage />);

    await waitFor(() => expect(adoptToken).toHaveBeenCalledWith("jwt-abc"));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/"));
  });

  it("scrubs the token from the address bar once adopted", async () => {
    // The fragment is not sent to servers, but it stays in history and in
    // anything the user copies out of the address bar.
    useAuthMock.mockReturnValue(auth());
    setUrl("", "#slack_token=jwt-abc");

    render(<LoginPage />);

    await waitFor(() => expect(window.location.hash).toBe(""));
  });

  it("explains an unlinked Slack account instead of a generic failure", async () => {
    useAuthMock.mockReturnValue(auth());
    setUrl("?slack=unlinked", "");

    render(<LoginPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/連携/);
  });

  it("offers the Slack button and navigates to the URL the backend mints", async () => {
    useAuthMock.mockReturnValue(auth());
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, href: "" });

    render(<LoginPage />);

    const button = await screen.findByRole("button", { name: /Slack/ });
    fireEvent.click(button);

    // Assert the NAVIGATION, not that the URL was fetched: the fetch happens on
    // mount regardless, so checking it would pass even with an empty onClick.
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("https://slack.com/oauth/v2/authorize?x=1"),
    );
    vi.unstubAllGlobals();
  });

  it("hides the Slack button when the backend says the feature is off", async () => {
    useAuthMock.mockReturnValue(auth());
    getSlackLoginUrlMock.mockRejectedValue(new Error("503"));

    render(<LoginPage />);

    // Password login must still work — Slack being unavailable is not fatal.
    expect(await screen.findByLabelText("メールアドレス")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Slack/ })).not.toBeInTheDocument(),
    );
  });
});
