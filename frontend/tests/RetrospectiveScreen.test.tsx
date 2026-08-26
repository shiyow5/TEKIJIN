import { RetrospectiveScreen } from "@/components/RetrospectiveScreen";
import type { ConsultRetrospectiveContext } from "@/lib/api-types";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getRetrospectiveContextMock = vi.fn();
const getTopicsMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getRetrospectiveContext: (...args: unknown[]) => getRetrospectiveContextMock(...args),
  getTopics: (...args: unknown[]) => getTopicsMock(...args),
  postConsultRetrospective: vi.fn(),
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
  getTopicsMock.mockReset();
  getTopicsMock.mockResolvedValue(["ネットワーク・VPN"]);
});

describe("RetrospectiveScreen", () => {
  it("renders the form for an accepted direct consultation", async () => {
    getRetrospectiveContextMock.mockResolvedValue(context());
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByText("直接相談のふりかえり")).toBeTruthy();
    expect(screen.getByText(/山田 太郎/)).toBeTruthy();
  });

  it("does not offer the form for a chat hand-off", async () => {
    // A chat consultation already leaves a transcript; a hearsay write-up on top
    // of it would be a second, weaker record of the same conversation.
    getRetrospectiveContextMock.mockResolvedValue(context({ consult_method: "chat" }));
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByText(/チャットのやり取りが残って/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /記録する/ })).toBeNull();
  });

  it("explains rather than offering a form nobody has accepted yet", async () => {
    getRetrospectiveContextMock.mockResolvedValue(context({ responder: null }));
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByText(/まだ受諾されていない/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /記録する/ })).toBeNull();
  });

  it("says so when the write-up has already been recorded", async () => {
    getRetrospectiveContextMock.mockResolvedValue(context({ already_recorded: true }));
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByText(/すでに記録されています/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /記録する/ })).toBeNull();
  });

  it("surfaces a load failure", async () => {
    getRetrospectiveContextMock.mockRejectedValue(new Error("boom"));
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
