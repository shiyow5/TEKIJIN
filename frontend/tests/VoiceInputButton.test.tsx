import { VoiceInputButton } from "@/components/VoiceInputButton";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

describe("VoiceInputButton", () => {
  it("renders an accessible voice-input button", () => {
    render(<VoiceInputButton />);
    expect(screen.getByRole("button", { name: /音声入力/ })).toBeInTheDocument();
  });

  it("calls onActivate when clicked", () => {
    const onActivate = vi.fn();
    render(<VoiceInputButton onActivate={onActivate} />);
    fireEvent.click(screen.getByRole("button", { name: /音声入力/ }));
    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it("can be disabled", () => {
    render(<VoiceInputButton disabled />);
    expect(screen.getByRole("button", { name: /音声入力/ })).toBeDisabled();
  });
});
