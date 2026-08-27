import { ModalDialog } from "@/components/ModalDialog";
import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";

function Harness({
  onCancel,
  wrapperClassName,
}: {
  onCancel: () => void;
  /** Simulates a caller mounted inside a positioned, z-indexed ancestor
   * (e.g. `HistoryRowOptionsMenu`'s `absolute z-10` container). */
  wrapperClassName?: string;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  return (
    <div data-testid="wrapper" className={wrapperClassName}>
      <ModalDialog titleId="t" onCancel={onCancel} initialFocusRef={confirmRef}>
        <h2 id="t">確認</h2>
        <button ref={confirmRef} type="button">
          確認する
        </button>
      </ModalDialog>
    </div>
  );
}

describe("ModalDialog", () => {
  it("renders via a portal into document.body, escaping a positioned/z-indexed ancestor (#397 follow-up)", () => {
    render(<Harness onCancel={vi.fn()} wrapperClassName="absolute z-10" />);
    const dialog = screen.getByRole("dialog");
    const wrapper = screen.getByTestId("wrapper");
    // The whole point of the portal: the overlay must NOT be a descendant of a
    // caller's positioned/z-indexed container, or that container's stacking
    // context traps it — letting a later sibling elsewhere in the page paint
    // over the "open" dialog and its backdrop.
    expect(wrapper.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
  });

  it("offers a corner close button, distinct from any 閉じる text button a caller's content supplies", () => {
    const onCancel = vi.fn();
    render(<Harness onCancel={onCancel} />);
    const closeButton = screen.getByRole("button", { name: "ダイアログを閉じる" });
    fireEvent.click(closeButton);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
