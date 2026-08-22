import { DraftEditor } from "@/components/result/DraftEditor";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

describe("DraftEditor", () => {
  it("seeds the textarea from the initial draft and sends the trimmed text", () => {
    const onSend = vi.fn();
    render(<DraftEditor initialDraft="  お世話になっております。  " onSend={onSend} />);

    const textarea = screen.getByLabelText<HTMLTextAreaElement>("聞き方の下書き");
    expect(textarea.value).toBe("  お世話になっております。  ");
    fireEvent.click(screen.getByRole("button", { name: "この方に送る" }));
    expect(onSend).toHaveBeenCalledWith("お世話になっております。");
  });

  it("disables send while the draft is empty or whitespace-only", () => {
    render(<DraftEditor initialDraft="   " onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "この方に送る" })).toBeDisabled();
  });

  it("reflects a later draft that arrives over SSE while untouched", () => {
    // The CTA can render on `recommend`, before the `draft` event — so the editor
    // opens empty and the real draft streams in afterward.
    const { rerender } = render(<DraftEditor initialDraft="" onSend={vi.fn()} />);
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("聞き方の下書き");
    expect(textarea.value).toBe("");

    rerender(<DraftEditor initialDraft="AIが生成した下書き" onSend={vi.fn()} />);
    expect(textarea.value).toBe("AIが生成した下書き");
  });

  it("never clobbers text the user has started editing when a late draft arrives", () => {
    const { rerender } = render(<DraftEditor initialDraft="" onSend={vi.fn()} />);
    const textarea = screen.getByLabelText<HTMLTextAreaElement>("聞き方の下書き");

    fireEvent.change(textarea, { target: { value: "自分で書いた本文" } });
    expect(textarea.value).toBe("自分で書いた本文");

    // A late draft must not overwrite the user's in-progress edit.
    rerender(<DraftEditor initialDraft="遅れて届いた下書き" onSend={vi.fn()} />);
    expect(textarea.value).toBe("自分で書いた本文");
  });

  it("honours an explicit disabled prop", () => {
    render(<DraftEditor initialDraft="本文" disabled onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "この方に送る" })).toBeDisabled();
  });
});
