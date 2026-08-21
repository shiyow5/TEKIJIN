import SessionPage from "@/../app/session/[id]/page";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("SessionPage (placeholder)", () => {
  it("renders the processing placeholder with the session id", async () => {
    const ui = await SessionPage({ params: Promise.resolve({ id: "abc-123" }) });
    render(ui);

    expect(screen.getByRole("heading", { name: "処理中…" })).toBeInTheDocument();
    expect(screen.getByTestId("session-id")).toHaveTextContent("abc-123");
  });
});
