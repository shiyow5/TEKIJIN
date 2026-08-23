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
});
