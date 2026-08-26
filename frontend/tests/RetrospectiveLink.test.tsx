import { RetrospectiveLink } from "@/components/RetrospectiveLink";
import type { ConsultRetrospectiveContext } from "@/lib/api-types";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getRetrospectiveContextMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getRetrospectiveContext: (...args: unknown[]) => getRetrospectiveContextMock(...args),
}));

function context(over: Partial<ConsultRetrospectiveContext> = {}): ConsultRetrospectiveContext {
  return {
    session_id: "s1",
    question_id: "q_0001",
    question: "拠点間VPNが不安定です",
    consult_method: "direct",
    responder: { person_id: "E001", name: "山田 太郎" },
    already_recorded: false,
    ...over,
  };
}

beforeEach(() => {
  getRetrospectiveContextMock.mockReset();
});

describe("RetrospectiveLink", () => {
  it("links to the retrospective for an accepted direct consultation", async () => {
    getRetrospectiveContextMock.mockResolvedValue(context());
    render(<RetrospectiveLink sessionId="s1" />);
    const link = await screen.findByRole("link", { name: /ふりかえりを記録/ });
    expect(link.getAttribute("href")).toBe("/session/s1/retrospective");
  });

  it("reads the durable context, not the pending hand-off view", async () => {
    // GET /handoff 404s the moment the responder records an outcome, i.e. exactly
    // when the face-to-face consultation becomes possible. A CTA built on it could
    // only ever appear before there was anything to write up.
    getRetrospectiveContextMock.mockResolvedValue(context());
    render(<RetrospectiveLink sessionId="s1" />);
    await screen.findByRole("link", { name: /ふりかえりを記録/ });
    expect(getRetrospectiveContextMock).toHaveBeenCalledWith("s1");
  });

  it("renders nothing for a chat hand-off", async () => {
    getRetrospectiveContextMock.mockResolvedValue(context({ consult_method: "chat" }));
    const { container } = render(<RetrospectiveLink sessionId="s1" />);
    await waitFor(() => expect(getRetrospectiveContextMock).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("renders nothing before anyone has accepted the hand-off", async () => {
    // Nothing has been consulted yet, so there is nothing to write up.
    getRetrospectiveContextMock.mockResolvedValue(context({ responder: null }));
    const { container } = render(<RetrospectiveLink sessionId="s1" />);
    await waitFor(() => expect(getRetrospectiveContextMock).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("renders nothing once a write-up already exists", async () => {
    getRetrospectiveContextMock.mockResolvedValue(context({ already_recorded: true }));
    const { container } = render(<RetrospectiveLink sessionId="s1" />);
    await waitFor(() => expect(getRetrospectiveContextMock).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("renders nothing when the context cannot be read", async () => {
    // The CTA is an extra: a failed lookup must stay silent rather than push an
    // error onto a screen that is otherwise fine.
    getRetrospectiveContextMock.mockRejectedValue(new Error("boom"));
    const { container } = render(<RetrospectiveLink sessionId="s1" />);
    await waitFor(() => expect(getRetrospectiveContextMock).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("renders nothing without a session id", () => {
    const { container } = render(<RetrospectiveLink />);
    expect(getRetrospectiveContextMock).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
  });
});
