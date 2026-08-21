import { AppHeader } from "@/components/AppHeader";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("AppHeader", () => {
  it("renders the product name", () => {
    render(<AppHeader />);
    expect(screen.getByText("TEKIJIN")).toBeInTheDocument();
    expect(screen.getByText("たずねーる")).toBeInTheDocument();
  });

  it("renders a user-switch control", () => {
    render(<AppHeader />);
    const select = screen.getByRole("combobox", { name: "ユーザー切替" });
    expect(select).toBeInTheDocument();
    expect(select.querySelectorAll("option").length).toBeGreaterThan(1);
  });
});
