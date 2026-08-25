import HomePage from "@/../app/page";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("HomePage (hub)", () => {
  it("links only to real, existing routes (#121: no /results or /answers 404s)", () => {
    render(<HomePage />);
    const hrefs = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"))
      .filter(Boolean);

    expect(hrefs).toContain("/questions");
    expect(hrefs).toContain("/inbox");
    expect(hrefs).toContain("/dashboard");
    // The old placeholder dead links must be gone.
    expect(hrefs).not.toContain("/results");
    expect(hrefs).not.toContain("/answers");
  });

  it("no longer describes itself as a placeholder", () => {
    render(<HomePage />);
    expect(screen.queryByText(/プレースホルダ/)).toBeNull();
  });

  it("presents the product promise, a primary CTA, and a how-it-works strip", () => {
    render(<HomePage />);
    expect(screen.getByRole("heading", { level: 1, name: /TEKIJIN/ })).toBeInTheDocument();
    // The hero CTA and the "質問する" action card both link to /questions.
    const ask = screen.getAllByRole("link", { name: /質問する/ });
    expect(ask.length).toBeGreaterThanOrEqual(1);
    expect(ask.every((a) => a.getAttribute("href") === "/questions")).toBe(true);
    expect(screen.getByRole("heading", { name: "使い方" })).toBeInTheDocument();
  });

  it("no longer claims the answer source is always a person (#292/#324)", () => {
    render(<HomePage />);
    expect(screen.queryByText("回答の出所は、常に人。")).toBeNull();
    expect(screen.queryByText(/AIは代わりに答えません/)).toBeNull();
  });

  it("frames the how-it-works flow as today's shape, not the only one (#337)", () => {
    render(<HomePage />);
    // The 3-step strip itself still describes today's live flow ("a person
    // answers") — this note keeps it from reading as the only possible one.
    expect(
      screen.getByText(/AIが出典つきで直接答えられる場面も少しずつ増えていきます/),
    ).toBeInTheDocument();
  });
});
