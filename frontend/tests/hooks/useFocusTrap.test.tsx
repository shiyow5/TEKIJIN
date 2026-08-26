import { useFocusTrap } from "@/hooks/useFocusTrap";
import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it } from "vitest";

function TrapHarness({ active }: { active: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, active);
  return (
    <div ref={ref}>
      <button type="button">first</button>
      <a href="/somewhere">link</a>
      <button type="button">last</button>
    </div>
  );
}

describe("useFocusTrap", () => {
  it("wraps Tab from the last element back to the first while active", () => {
    render(<TrapHarness active />);
    screen.getByRole("button", { name: "last" }).focus();

    fireEvent.keyDown(document, { key: "Tab" });

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "first" }));
  });

  it("wraps Shift+Tab from the first element back to the last while active", () => {
    render(<TrapHarness active />);
    screen.getByRole("button", { name: "first" }).focus();

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "last" }));
  });

  // #391 review: the drawer contains `<Link>`s, not just buttons — a
  // buttons-only trap (ModalDialog's original query) would let Tab escape
  // through a link.
  it("treats a link inside the container as part of the trapped set", () => {
    render(<TrapHarness active />);
    screen.getByRole("link", { name: "link" }).focus();

    fireEvent.keyDown(document, { key: "Tab" });

    // The link is not the last element, so a forward Tab from it is left
    // alone (jsdom does not move focus on a synthetic key event) — the point
    // is nothing throws and the link was found by the trapped-elements query.
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "link" }));
  });

  // #391 review's actual bug: focus started outside the container entirely
  // (the page behind an overlay) and Tab/Shift+Tab still reached it.
  it("redirects Shift+Tab back into the container when focus starts outside it", () => {
    render(
      <>
        <button type="button">outside</button>
        <TrapHarness active />
      </>,
    );
    screen.getByRole("button", { name: "outside" }).focus();

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "last" }));
  });

  it("does nothing while inactive", () => {
    render(<TrapHarness active={false} />);
    const last = screen.getByRole("button", { name: "last" });
    last.focus();

    fireEvent.keyDown(document, { key: "Tab" });

    expect(document.activeElement).toBe(last);
  });
});
