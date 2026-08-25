"use client";

/**
 * Confirmation modal for deleting a past question (#286).
 *
 * Replaces the old inline "削除しますか？" popover (#207), which sat inside the
 * card and was easy to miss in a dense list. Deleting is not undoable and now
 * cascades to the question's answers, recommendations, events, and chat
 * history, so the confirmation is a real modal: it names the question, blocks
 * the rest of the page while open, and can be dismissed via 「やめる」, a
 * backdrop click, or Escape.
 */

import { useEffect, useRef } from "react";

export interface QuestionDeleteDialogProps {
  title: string;
  onConfirm: () => void;
  onCancel: () => void;
  disabled?: boolean;
}

export function QuestionDeleteDialog({
  title,
  onConfirm,
  onCancel,
  disabled = false,
}: QuestionDeleteDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  // Same focus contract as ConsultMethodDialog: move focus in on open (onto
  // the safe/cancel action, since this confirms a destructive delete), keep
  // Tab inside, and hand focus back to whatever opened us (the ✕ button) on
  // close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    cancelButtonRef.current?.focus();
    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !disabled) {
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
  }, [onCancel, disabled]);

  return (
    /* biome-ignore lint/a11y/useKeyWithClickEvents: mouse-only dismissal; the
    keyboard path is the document-level Escape listener registered above, not
    a key event on this element. */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-on-surface/40 p-md"
      onClick={() => {
        if (!disabled) onCancel();
      }}
    >
      {/* biome-ignore lint/a11y/useSemanticElements: role="dialog" + aria-modal
      matches this component's own onCancel/Escape handling; the native
      <dialog> element's imperative showModal()/close() API isn't needed here.
      biome-ignore lint/a11y/useKeyWithClickEvents: onClick here only stops the
      backdrop click from bubbling; it is not itself an interactive action. */}
      <div
        role="dialog"
        ref={dialogRef}
        aria-modal="true"
        aria-labelledby="question-delete-dialog-title"
        onClick={(e) => e.stopPropagation()}
        className="flex w-full max-w-sm flex-col gap-md rounded-xl border border-outline-variant bg-surface-container-lowest p-lg shadow-md"
      >
        <h2 id="question-delete-dialog-title" className="font-bold text-lg text-on-surface">
          削除しますか？
        </h2>
        <p className="text-on-surface-variant text-sm">
          「{title}」を削除します。回答・チャット履歴を含め、元に戻せません。
        </p>
        <div className="flex justify-end gap-sm">
          <button
            ref={cancelButtonRef}
            type="button"
            disabled={disabled}
            onClick={onCancel}
            className="rounded-lg px-md py-sm text-on-surface-variant text-sm hover:underline disabled:cursor-not-allowed disabled:opacity-50"
          >
            やめる
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={onConfirm}
            className="rounded-lg bg-error px-md py-sm font-bold text-on-error text-sm shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            削除
          </button>
        </div>
      </div>
    </div>
  );
}
