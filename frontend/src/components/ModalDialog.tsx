"use client";

/**
 * Shared modal-dialog chrome: overlay, `role="dialog"` + `aria-modal`, Tab-trap,
 * Escape-to-cancel, and opener focus restore. Extracted from
 * `result/ConsultMethodDialog.tsx` and `QuestionResolveButton`'s confirm popup,
 * which had independently reimplemented the same a11y-sensitive logic and had
 * already drifted (their Tab-trap selectors disagreed on whether to skip
 * disabled buttons).
 *
 * Callers must NOT disable the element that opened the dialog in the same
 * render that mounts it: per the HTML spec, a browser blurs a focused control
 * the instant it becomes disabled, and that happens during DOM commit — before
 * this component's opener-capture effect gets a chance to run. Capturing would
 * then see `document.body` instead of the real opener, and focus could never
 * be restored to it on close.
 */

import { type ReactNode, type RefObject, useEffect, useRef } from "react";

export interface ModalDialogProps {
  titleId: string;
  onCancel: () => void;
  initialFocusRef: RefObject<HTMLElement | null>;
  children: ReactNode;
}

export function ModalDialog({ titleId, onCancel, initialFocusRef, children }: ModalDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-on-surface/40 p-md">
      {/* biome-ignore lint/a11y/useSemanticElements: role="dialog" + aria-modal
      matches this component's own onCancel/Escape handling; the native
      <dialog> element's imperative showModal()/close() API isn't needed here. */}
      <div
        role="dialog"
        ref={dialogRef}
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex w-full max-w-sm flex-col gap-md rounded-xl border border-outline-variant bg-surface-container-lowest p-lg shadow-md"
      >
        {children}
      </div>
    </div>
  );
}
