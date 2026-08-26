import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { NotificationBell } from "@/components/NotificationBell";
import type { DeclineNotification } from "@/lib/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useCurrentUserMock = vi.fn<() => CurrentUserContextValue>();
vi.mock("@/components/CurrentUserProvider", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

const getNotificationsMock = vi.fn();
const ackNotificationsMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getNotifications: (...args: unknown[]) => getNotificationsMock(...args),
  ackNotifications: (...args: unknown[]) => ackNotificationsMock(...args),
}));

function asUser(id: string | null): CurrentUserContextValue {
  return {
    employees: [],
    currentUserId: id,
    currentUser: null,
    setCurrentUserId: vi.fn(),
    loading: false,
    error: false,
    reload: vi.fn(),
    canSwitch: false,
  };
}

const NOTIFICATION: DeclineNotification = {
  id: 1,
  question_id: "q1",
  session_id: "s1",
  message: "田中さんに断られたので次の候補に依頼してください",
  declined_person_name: "田中",
  created_at: "2026-08-20T10:00:00",
};

beforeEach(() => {
  getNotificationsMock.mockReset();
  ackNotificationsMock.mockReset();
  ackNotificationsMock.mockResolvedValue({ acknowledged: 1 });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("NotificationBell", () => {
  it("renders nothing before a current user is resolved", () => {
    useCurrentUserMock.mockReturnValue(asUser(null));
    render(<NotificationBell />);
    expect(getNotificationsMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("fetches notifications for the acting user and shows an unread badge", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getNotificationsMock.mockResolvedValue([NOTIFICATION]);
    render(<NotificationBell />);
    await waitFor(() => expect(getNotificationsMock).toHaveBeenCalledWith("E010"));
    expect(await screen.findByRole("button", { name: "通知（未読1件）" })).toBeInTheDocument();
  });

  it("draws the bell with the same stroked icon idiom as the other header icons (#346)", async () => {
    // It used to be a literal 🔔 emoji, which renders in the platform's own colour
    // and weight: it cannot inherit `currentColor`, ignores `strokeWidth`, and so
    // never matched the nav icons beside it — nor followed the dark theme.
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getNotificationsMock.mockResolvedValue([]);
    const { container } = render(<NotificationBell />);
    await screen.findByRole("button", { name: "通知" });

    const icon = container.querySelector("svg");
    expect(icon).not.toBeNull();
    expect(icon).toHaveAttribute("stroke", "currentColor");
    expect(icon).toHaveAttribute("stroke-width", "1.8");
    expect(icon).toHaveAttribute("viewBox", "0 0 24 24");
    expect(icon?.getAttribute("class")).toContain("h-5 w-5");
    expect(container.textContent).not.toContain("🔔");
  });

  it("shows no badge and a plain label when there are no notifications", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getNotificationsMock.mockResolvedValue([]);
    render(<NotificationBell />);
    await waitFor(() => expect(getNotificationsMock).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "通知" })).toBeInTheDocument();
  });

  it("opens the dropdown and shows a deep link to the session", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getNotificationsMock.mockResolvedValue([NOTIFICATION]);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    const link = screen.getByRole("link", { name: NOTIFICATION.message });
    expect(link).toHaveAttribute("href", "/session/s1");
  });

  it("acknowledges a notification when its link is opened, removing it locally", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getNotificationsMock.mockResolvedValue([NOTIFICATION]);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    fireEvent.click(screen.getByRole("link", { name: NOTIFICATION.message }));

    await waitFor(() =>
      expect(ackNotificationsMock).toHaveBeenCalledWith({ asker_id: "E010", ids: [1] }),
    );
    // Optimistically removed -> badge drops to the no-count state.
    expect(screen.getByRole("button", { name: "通知" })).toBeInTheDocument();
  });

  it("shows an acknowledge button instead of a link for a notification with no session", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getNotificationsMock.mockResolvedValue([{ ...NOTIFICATION, session_id: null }]);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    expect(screen.queryByRole("link", { name: NOTIFICATION.message })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "既読にする" }));
    await waitFor(() =>
      expect(ackNotificationsMock).toHaveBeenCalledWith({ asker_id: "E010", ids: [1] }),
    );
  });

  it("polls again after the interval elapses", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getNotificationsMock.mockResolvedValue([]);
    render(<NotificationBell />);
    await vi.waitFor(() => expect(getNotificationsMock).toHaveBeenCalledTimes(1));

    await vi.advanceTimersByTimeAsync(15_000);
    expect(getNotificationsMock).toHaveBeenCalledTimes(2);
  });
});
