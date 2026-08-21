import SessionPage from "@/../app/session/[id]/page";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The screen itself subscribes to EventSource (client-only); the page is just a
// server wrapper, so we stub the screen and assert the id is threaded through.
vi.mock("@/components/ProcessingScreen", () => ({
  ProcessingScreen: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="processing-screen">{sessionId}</div>
  ),
}));

describe("SessionPage", () => {
  it("resolves the route param and renders ProcessingScreen with the session id", async () => {
    const ui = await SessionPage({ params: Promise.resolve({ id: "abc-123" }) });
    render(ui);

    expect(screen.getByTestId("processing-screen")).toHaveTextContent("abc-123");
  });
});
