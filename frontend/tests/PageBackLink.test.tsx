import { PageBackLink } from "@/components/PageBackLink";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("PageBackLink", () => {
  it("shows an explicit destination with a keyboard-sized link target", () => {
    render(<PageBackLink href="/" label="ホームへ戻る" />);

    const link = screen.getByRole("link", { name: "ホームへ戻る" });
    expect(link).toHaveAttribute("href", "/");
    expect(link).toHaveClass("min-h-[44px]");
    expect(link.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
});
