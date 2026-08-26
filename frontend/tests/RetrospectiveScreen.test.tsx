import { RetrospectiveScreen } from "@/components/RetrospectiveScreen";
import type { HandoffResponse } from "@/lib/api-types";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getHandoffMock = vi.fn();
const getTopicsMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getHandoff: (...args: unknown[]) => getHandoffMock(...args),
  getTopics: (...args: unknown[]) => getTopicsMock(...args),
  postConsultRetrospective: vi.fn(),
}));

function handoff(over: Partial<HandoffResponse> = {}): HandoffResponse {
  return {
    session_id: "s1",
    question: "拠点間VPNが不安定です",
    question_id: "q_0001",
    asker: { id: "E010", name: "相談者", dept: "営業部" },
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
  getTopicsMock.mockReset();
  getTopicsMock.mockResolvedValue(["ネットワーク・VPN"]);
});

describe("RetrospectiveScreen", () => {
  it("renders the form for a direct consultation", async () => {
    getHandoffMock.mockResolvedValue(handoff());
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByText("直接相談のふりかえり")).toBeTruthy();
    expect(screen.getByText(/山田 太郎/)).toBeTruthy();
  });

  it("does not offer the form for a chat hand-off", async () => {
    // A chat consultation already leaves a transcript; a hearsay write-up on top
    // of it would be a second, weaker record of the same conversation.
    getHandoffMock.mockResolvedValue(handoff({ consult_method: "chat" }));
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByText(/チャットのやり取りが残って/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /記録する/ })).toBeNull();
  });

  it("explains rather than crashing when the hand-off has no responder", async () => {
    getHandoffMock.mockResolvedValue(handoff({ responder: null }));
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("explains rather than crashing when the question id is missing", async () => {
    getHandoffMock.mockResolvedValue(handoff({ question_id: null }));
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("surfaces a load failure", async () => {
    getHandoffMock.mockRejectedValue(new Error("boom"));
    render(<RetrospectiveScreen sessionId="s1" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
