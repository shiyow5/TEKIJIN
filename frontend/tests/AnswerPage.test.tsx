import AnswerPage from "@/../app/answer/[session_id]/page";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/AnswerScreen", () => ({
  AnswerScreen: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="answer-screen">{sessionId}</div>
  ),
}));

describe("AnswerPage", () => {
  it("keeps a standalone answer deep link connected to the inbox", async () => {
    const page = await AnswerPage({ params: Promise.resolve({ session_id: "sess-42" }) });
    render(page);

    expect(screen.getByRole("link", { name: "受信箱へ戻る" })).toHaveAttribute("href", "/inbox");
    expect(screen.getByTestId("answer-screen")).toHaveTextContent("sess-42");
  });
});
