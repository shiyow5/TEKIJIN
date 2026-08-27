import type { CurrentUserContextValue } from "@/components/CurrentUserProvider";
import { NotificationBell } from "@/components/NotificationBell";
import type { Notification } from "@/lib/api-types";
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

/** Every acting user polls both scopes (#509); route each call by its param shape. */
function mockNotifications(asker: Notification[], responder: Notification[] = []) {
  getNotificationsMock.mockImplementation(
    (params: { askerId?: string } | { employeeId?: string }) =>
      Promise.resolve("askerId" in params ? asker : responder),
  );
}

const DECLINED: Notification = {
  kind: "declined",
  id: 1,
  question_id: "q1",
  session_id: "s1",
  message: "田中さんに断られたので次の候補に依頼してください",
  declined_person_name: "田中",
  created_at: "2026-08-20T10:00:00",
};

const ACCEPTED_CHAT: Notification = {
  kind: "accepted",
  id: 2,
  question_id: "q2",
  session_id: "s2",
  message: "鈴木さんが依頼を受け取りました",
  responder_name: "鈴木",
  consult_method: "chat",
  created_at: "2026-08-21T10:00:00",
};

const ACCEPTED_DIRECT: Notification = {
  kind: "accepted",
  id: 3,
  question_id: "q3",
  session_id: "s3",
  message: "佐藤さんが依頼を受け取りました",
  responder_name: "佐藤",
  consult_method: "direct",
  created_at: "2026-08-21T11:00:00",
};

const REQUEST_RECEIVED: Notification = {
  kind: "request_received",
  id: 4,
  question_id: "q4",
  session_id: "s4",
  message: "山田さんから新しい依頼が届きました",
  asker_name: "山田",
  created_at: "2026-08-22T10:00:00",
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

  it("fetches both roles (asker + responder) and shows a merged unread badge (#509)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([DECLINED], [REQUEST_RECEIVED]);
    render(<NotificationBell />);
    await waitFor(() => expect(getNotificationsMock).toHaveBeenCalledWith({ askerId: "E010" }));
    expect(getNotificationsMock).toHaveBeenCalledWith({ employeeId: "E010" });
    expect(await screen.findByRole("button", { name: "通知（未読2件）" })).toBeInTheDocument();
  });

  it("still shows one scope when the other notification fetch fails", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    getNotificationsMock.mockImplementation(
      (params: { askerId?: string } | { employeeId?: string }) =>
        "askerId" in params
          ? Promise.reject(new Error("asker notifications unavailable"))
          : Promise.resolve([REQUEST_RECEIVED]),
    );
    render(<NotificationBell />);
    expect(await screen.findByRole("button", { name: "通知（未読1件）" })).toBeInTheDocument();
  });

  it("uses the same stroked icon idiom as the other header icons (#346)", async () => {
    // It used to be a literal 🔔 emoji, drawn from a colour-emoji font: it ignores
    // `color` and has no stroke to weight, so it could not match the nav icons at
    // any value. This pins the IDIOM, not the drawing — a square with the same
    // attributes would pass, and asserting the path data would be brittle and
    // worthless. The bell shape is a visual review's job.
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([], []);
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
    mockNotifications([], []);
    render(<NotificationBell />);
    await waitFor(() => expect(getNotificationsMock).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "通知" })).toBeInTheDocument();
  });

  it("opens the dropdown and shows a deep link to the session for a decline (#E7)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([DECLINED], []);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    const link = screen.getByRole("link", { name: DECLINED.message });
    expect(link).toHaveAttribute("href", "/session/s1");
  });

  it("links an accepted+chat notification to the chat thread (#509)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([ACCEPTED_CHAT], []);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    expect(screen.getByRole("link", { name: ACCEPTED_CHAT.message })).toHaveAttribute(
      "href",
      "/chat?thread=2",
    );
  });

  it("links an accepted+direct notification straight to the session (#509)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([ACCEPTED_DIRECT], []);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    expect(screen.getByRole("link", { name: ACCEPTED_DIRECT.message })).toHaveAttribute(
      "href",
      "/session/s3",
    );
  });

  it("links a request_received notification to the inbox (#509)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([], [REQUEST_RECEIVED]);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    expect(screen.getByRole("link", { name: REQUEST_RECEIVED.message })).toHaveAttribute(
      "href",
      "/inbox",
    );
  });

  it("acknowledges a decline notification when its link is opened, removing it locally", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([DECLINED], []);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    fireEvent.click(screen.getByRole("link", { name: DECLINED.message }));

    await waitFor(() =>
      expect(ackNotificationsMock).toHaveBeenCalledWith({
        kind: "declined",
        asker_id: "E010",
        ids: [1],
      }),
    );
    // Optimistically removed -> badge drops to the no-count state.
    expect(screen.getByRole("button", { name: "通知" })).toBeInTheDocument();
  });

  it("does not resurrect an acknowledged item from a stale later poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    // Deliberately keep returning the pre-ack snapshot to model a poll that
    // started before POST /notifications/ack committed.
    mockNotifications([DECLINED], []);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    fireEvent.click(screen.getByRole("link", { name: DECLINED.message }));
    await waitFor(() => expect(ackNotificationsMock).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "通知" })).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(15_000);
    expect(screen.getByRole("button", { name: "通知" })).toBeInTheDocument();
  });

  it("acks an accepted notification with the asker id (#509)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([ACCEPTED_CHAT], []);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    fireEvent.click(screen.getByRole("link", { name: ACCEPTED_CHAT.message }));

    await waitFor(() =>
      expect(ackNotificationsMock).toHaveBeenCalledWith({
        kind: "accepted",
        asker_id: "E010",
        ids: [2],
      }),
    );
  });

  it("acks a request_received notification with the employee id, not the asker id (#509)", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([], [REQUEST_RECEIVED]);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    fireEvent.click(screen.getByRole("link", { name: REQUEST_RECEIVED.message }));

    await waitFor(() =>
      expect(ackNotificationsMock).toHaveBeenCalledWith({
        kind: "request_received",
        employee_id: "E010",
        ids: [4],
      }),
    );
  });

  it("shows an acknowledge button instead of a link for a decline with no session", async () => {
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([{ ...DECLINED, session_id: null }], []);
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "通知（未読1件）" }));
    expect(screen.queryByRole("link", { name: DECLINED.message })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "既読にする" }));
    await waitFor(() =>
      expect(ackNotificationsMock).toHaveBeenCalledWith({
        kind: "declined",
        asker_id: "E010",
        ids: [1],
      }),
    );
  });

  it("polls again after the interval elapses, for both roles", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useCurrentUserMock.mockReturnValue(asUser("E010"));
    mockNotifications([], []);
    render(<NotificationBell />);
    // One asker-scoped + one responder-scoped call per poll cycle.
    await vi.waitFor(() => expect(getNotificationsMock).toHaveBeenCalledTimes(2));

    await vi.advanceTimersByTimeAsync(15_000);
    expect(getNotificationsMock).toHaveBeenCalledTimes(4);
  });
});
