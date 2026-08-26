"use client";

/**
 * Shared modal-dialog chrome: overlay, `role="dialog"` + `aria-modal`, Tab-trap,
 * Escape-to-cancel, and opener focus restore. Extracted from
 * `result/ConsultMethodDialog.tsx` when `QuestionResolveButton`'s confirmation
 * became a popup too (#289) and would otherwise have carried a second copy of
 * the same a11y-sensitive logic. Anything that needs a modal should render this
 * rather than reimplement it — #286 (delete confirmation) is the third caller.
 *
 * Backdrop-click dismissal is opt-in via `dismissOnBackdrop`, because it is not
 * universally safe: it is right for a confirmation whose cancel path is free
 * (#286), and wrong wherever a stray click would discard work. Escape and the
 * dialog's own cancel control are always available regardless.
 *
 * Callers must NOT disable the element that opened the dialog in the same
 * render that mounts it: per the HTML spec, a browser blurs a focused control
 * the instant it becomes disabled, and that happens during DOM commit — before
 * this component's opener-capture effect gets a chance to run. Capturing would
 * then see `document.body` instead of the real opener, and focus could never
 * be restored to it on close.
 *
 * Rendered via a portal into `document.body`, not in place: a caller that
 * itself sits inside a `position`-and-`z-index` ancestor (e.g. one card's
 * `HistoryRowOptionsMenu`, `absolute z-10`) would otherwise trap this
 * `fixed inset-0` overlay inside that ancestor's own stacking context — CSS
 * then paints any LATER sibling at the same stacking level (a card further
 * down the list) on top of the whole trapped context, backdrop included, so
 * that sibling's own controls stay clickable through the "open" dialog. A
 * portal escapes every ancestor's stacking context, so the overlay is always
 * the topmost thing in the document regardless of where it is mounted from.
 */

import { type ReactNode, type RefObject, useEffect, useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";

export interface ModalDialogProps {
  titleId: string;
  onCancel: () => void;
  initialFocusRef: RefObject<HTMLElement | null>;
  /** Close when the overlay outside the dialog panel is clicked. Default off. */
  dismissOnBackdrop?: boolean;
  /**
   * Panel width. Defaults to the confirmation-dialog width every other caller
   * wants; override for content that needs more room (#392: 使い方's 3-column
   * step grid reads as cramped at the default width).
   */
  maxWidthClassName?: string;
  children: ReactNode;
}

export function ModalDialog({
  titleId,
  onCancel,
  initialFocusRef,
  dismissOnBackdrop = false,
  maxWidthClassName = "max-w-sm",
  children,
}: ModalDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  // A layout effect, not a plain effect: it must run before the browser paints,
  // or a caller that focuses its own trigger just before mounting this dialog
  // (so the opener capture below sees the right element — see e.g.
  // `HistoryRowOptionsMenu`) would paint one visible frame with the trigger's
  // focus ring still showing behind the already-open dialog.
  useLayoutEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    initialFocusRef.current?.focus();
    return () => opener?.focus?.();
  }, [initialFocusRef]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled])");
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !dialogRef.current?.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  return createPortal(
    /* biome-ignore lint/a11y/useKeyWithClickEvents: mouse-only dismissal; the
    keyboard path is the document-level Escape listener registered above, not
    a key event on this element. */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-on-surface/40 p-md"
      onClick={
        dismissOnBackdrop
          ? (e) => {
              // Only a click on the overlay itself. Clicks inside the panel
              // bubble up to here with a different target, and must not close.
              if (e.target === e.currentTarget) onCancel();
            }
          : undefined
      }
    >
      {/* biome-ignore lint/a11y/useSemanticElements: role="dialog" + aria-modal
      matches this component's own onCancel/Escape handling; the native
      <dialog> element's imperative showModal()/close() API isn't needed here. */}
      <div
        role="dialog"
        ref={dialogRef}
        aria-modal="true"
        aria-labelledby={titleId}
        className={`relative flex w-full ${maxWidthClassName} flex-col gap-md rounded-xl border border-outline-variant bg-surface-container-lowest p-lg shadow-md`}
      >
        <button
          type="button"
          onClick={onCancel}
          aria-label="ダイアログを閉じる"
          className="absolute top-sm right-sm flex h-8 w-8 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container-low hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            aria-hidden="true"
            className="h-5 w-5"
          >
            <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
          </svg>
        </button>
        {children}
      </div>
    </div>,
    document.body,
  );
}
