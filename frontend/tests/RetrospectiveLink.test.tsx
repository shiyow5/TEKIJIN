import { RetrospectiveLink } from "@/components/RetrospectiveLink";
import type { HandoffResponse } from "@/lib/api-types";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getHandoffMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  getHandoff: (...args: unknown[]) => getHandoffMock(...args),
}));

function handoff(over: Partial<HandoffResponse> = {}): HandoffResponse {
  return {
    session_id: "s1",
    question: "q",
    question_id: "q_0001",
    asker: { id: "E010", name: "相談者" },
    topics: [],
    products: [],
    missing: [],
    responder: { person_id: "E001", name: "山田 太郎", score: 0.8, reasons: [] },
    draft: "",
    reuse_count: 0,
    helpful_answer_count: 0,
    consult_method: "direct",
    ...over,
  } as HandoffResponse;
}

beforeEach(() => {
  getHandoffMock.mockReset();
});

describe("RetrospectiveLink", () => {
  it("links to the retrospective for a direct consultation", async () => {
    getHandoffMock.mockResolvedValue(handoff());
    render(<RetrospectiveLink sessionId="s1" />);
    const link = await screen.findByRole("link", { name: /ふりかえりを記録/ });
    expect(link.getAttribute("href")).toBe("/session/s1/retrospective");
  });

  it("renders nothing for a chat hand-off", async () => {
    getHandoffMock.mockResolvedValue(handoff({ consult_method: "chat" }));
    const { container } = render(<RetrospectiveLink sessionId="s1" />);
    await waitFor(() => expect(getHandoffMock).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("renders nothing when the hand-off cannot be read", async () => {
    // The CTA is an extra: a failed lookup must stay silent rather than push an
    // error onto a screen that is otherwise fine.
    getHandoffMock.mockRejectedValue(new Error("boom"));
    const { container } = render(<RetrospectiveLink sessionId="s1" />);
    await waitFor(() => expect(getHandoffMock).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("renders nothing without a session id", () => {
    const { container } = render(<RetrospectiveLink />);
    expect(getHandoffMock).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
  });
});
