import { HowItWorksButton } from "@/components/HowItWorksButton";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("HowItWorksButton", () => {
  it("starts closed, showing only the trigger", () => {
    render(<HowItWorksButton />);
    expect(screen.getByRole("button", { name: "使い方を見る" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens a dialog with the 3-step content and focuses the close button", () => {
    render(<HowItWorksButton />);
    fireEvent.click(screen.getByRole("button", { name: "使い方を見る" }));

    const dialog = screen.getByRole("dialog", { name: "使い方" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("質問を書く")).toBeInTheDocument();
    expect(screen.getByText("AIが取り次ぐ")).toBeInTheDocument();
    expect(screen.getByText("人が答える")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "閉じる" })).toHaveFocus();
  });

  it("closes on Escape", () => {
    render(<HowItWorksButton />);
    fireEvent.click(screen.getByRole("button", { name: "使い方を見る" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes when the 閉じる button is clicked", () => {
    render(<HowItWorksButton />);
    fireEvent.click(screen.getByRole("button", { name: "使い方を見る" }));
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
