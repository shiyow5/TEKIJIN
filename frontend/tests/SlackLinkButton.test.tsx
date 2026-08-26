import type { AuthContextValue } from "@/components/AuthProvider";
import { SlackLinkButton } from "@/components/SlackLinkButton";
import type { Principal } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useAuthMock = vi.fn<() => AuthContextValue>();
vi.mock("@/components/AuthProvider", () => ({
  useAuth: () => useAuthMock(),
}));

const getSlackStatusMock = vi.fn();
const getSlackAuthorizeUrlMock = vi.fn();
const postSlackUnlinkMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getSlackStatus: (...args: unknown[]) => getSlackStatusMock(...args),
  getSlackAuthorizeUrl: (...args: unknown[]) => getSlackAuthorizeUrlMock(...args),
  postSlackUnlink: (...args: unknown[]) => postSlackUnlinkMock(...args),
  ApiError: class ApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

const ADMIN: Principal = { id: null, name: "管理者", dept: null, is_admin: true };
const USER: Principal = { id: "E005", name: "山田 太郎", dept: "営業部", is_admin: false };

function auth(principal: Principal | null): AuthContextValue {
  return { principal, loading: false, login: vi.fn(), logout: vi.fn() };
}

const originalLocation = window.location;

beforeEach(() => {
  useAuthMock.mockReset();
  getSlackStatusMock.mockReset();
  getSlackAuthorizeUrlMock.mockReset();
  postSlackUnlinkMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  Object.defineProperty(window, "location", { value: originalLocation, writable: true });
});

describe("SlackLinkButton", () => {
  it("renders nothing for the admin principal", () => {
    useAuthMock.mockReturnValue(auth(ADMIN));
    const { container } = render(<SlackLinkButton />);
    expect(getSlackStatusMock).not.toHaveBeenCalled();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when logged out", () => {
    useAuthMock.mockReturnValue(auth(null));
    const { container } = render(<SlackLinkButton />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a connect button for an unlinked employee", async () => {
    useAuthMock.mockReturnValue(auth(USER));
    getSlackStatusMock.mockResolvedValue({ linked: false });
    render(<SlackLinkButton />);
    expect(await screen.findByRole("button", { name: "Slackと連携" })).toBeInTheDocument();
  });

  it("shows a linked badge and an unlink control when already linked", async () => {
    useAuthMock.mockReturnValue(auth(USER));
    getSlackStatusMock.mockResolvedValue({ linked: true, open_url: null });
    render(<SlackLinkButton />);
    expect(await screen.findByText("Slack連携済み")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "解除" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Slackで開く" })).not.toBeInTheDocument();
  });

  it("shows a link to open the linked employee's own Slack DM with the bot", async () => {
    useAuthMock.mockReturnValue(auth(USER));
    getSlackStatusMock.mockResolvedValue({
      linked: true,
      open_url: "https://slack.com/app_redirect?app=A1&team=T1",
    });
    render(<SlackLinkButton />);
    const link = await screen.findByRole("link", { name: "Slackで開く" });
    expect(link).toHaveAttribute("href", "https://slack.com/app_redirect?app=A1&team=T1");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("navigates the browser to the authorize URL when clicking connect", async () => {
    useAuthMock.mockReturnValue(auth(USER));
    getSlackStatusMock.mockResolvedValue({ linked: false });
    getSlackAuthorizeUrlMock.mockResolvedValue({ url: "https://slack.com/oauth/v2/authorize?x=1" });
    Object.defineProperty(window, "location", { value: { href: "" }, writable: true });

    render(<SlackLinkButton />);
    fireEvent.click(await screen.findByRole("button", { name: "Slackと連携" }));

    await waitFor(() =>
      expect(window.location.href).toBe("https://slack.com/oauth/v2/authorize?x=1"),
    );
  });

  it("shows an unavailable message when the backend has no Slack App configured (503)", async () => {
    const { ApiError } = await import("@/lib/api-client");
    useAuthMock.mockReturnValue(auth(USER));
    getSlackStatusMock.mockResolvedValue({ linked: false });
    getSlackAuthorizeUrlMock.mockRejectedValue(
      new ApiError(503, "Slack連携は現在利用できません。"),
    );

    render(<SlackLinkButton />);
    fireEvent.click(await screen.findByRole("button", { name: "Slackと連携" }));

    expect(await screen.findByText("Slack連携は現在利用できません")).toBeInTheDocument();
  });

  it("unlinks and falls back to the connect button", async () => {
    useAuthMock.mockReturnValue(auth(USER));
    getSlackStatusMock.mockResolvedValue({ linked: true, open_url: null });
    postSlackUnlinkMock.mockResolvedValue({ ok: true });

    render(<SlackLinkButton />);
    fireEvent.click(await screen.findByRole("button", { name: "解除" }));

    expect(await screen.findByRole("button", { name: "Slackと連携" })).toBeInTheDocument();
    expect(postSlackUnlinkMock).toHaveBeenCalledTimes(1);
  });
});
