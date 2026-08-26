"use client";

/**
 * Traps Tab / Shift+Tab focus cycling within `containerRef` while `active` is
 * true. Extracted from `ModalDialog` (#289) so a caller with different chrome
 * — a centred confirmation panel vs. a full-height slide-in drawer (#391
 * review) — can share the same keyboard boundary without adopting
 * `ModalDialog`'s whole layout. `ModalDialog` itself now uses this hook too,
 * so there is exactly one implementation of the trap.
 *
 * The focusable-element query covers links and form controls, not just
 * buttons: `ModalDialog`'s own callers only ever contain buttons, but a nav
 * drawer contains `<Link>`s as well — a buttons-only query would let Tab
 * escape through them.
 */

import { type RefObject, useEffect } from "react";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useFocusTrap(containerRef: RefObject<HTMLElement | null>, active: boolean) {
  useEffect(() => {
    if (!active) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== "Tab") return;
      const focusable = containerRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !containerRef.current?.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [containerRef, active]);
}
