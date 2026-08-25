import { PageBackLink } from "@/components/PageBackLink";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("PageBackLink", () => {
  it("shows an explicit destination with a keyboard-sized link target", () => {
    render(<PageBackLink href="/questions" label="質問一覧へ戻る" />);

    const link = screen.getByRole("link", { name: "質問一覧へ戻る" });
    expect(link).toHaveAttribute("href", "/questions");
    expect(link).toHaveClass("min-h-[44px]");
    expect(link.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
});
