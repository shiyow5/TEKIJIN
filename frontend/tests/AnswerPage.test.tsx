import AnswerPage from "@/../app/answer/[session_id]/page";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/AnswerScreen", () => ({
  AnswerScreen: ({ sessionId, showBackLink }: { sessionId: string; showBackLink?: boolean }) => (
    <div data-testid="answer-screen" data-show-back-link={showBackLink}>
      {sessionId}
    </div>
  ),
}));

describe("AnswerPage", () => {
  it("keeps a standalone answer deep link connected to the inbox", async () => {
    const page = await AnswerPage({ params: Promise.resolve({ session_id: "sess-42" }) });
    render(page);

    expect(screen.getByTestId("answer-screen")).toHaveTextContent("sess-42");
    expect(screen.getByTestId("answer-screen")).toHaveAttribute("data-show-back-link", "true");
  });
});
