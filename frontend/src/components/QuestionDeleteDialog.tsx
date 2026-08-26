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
 *
 * The dialog chrome (overlay, role/aria, Tab-trap, Escape, focus restore) comes
 * from `ModalDialog`; this component only supplies the copy and the buttons.
 * While the delete request is in flight every dismissal path is suppressed —
 * the call has already been sent, so closing the dialog would only hide it.
 */

import { ModalDialog } from "@/components/ModalDialog";
import { useRef } from "react";

export interface QuestionDeleteDialogProps {
  title: string;
  onConfirm: () => void;
  onCancel: () => void;
  disabled?: boolean;
}

const TITLE_ID = "question-delete-dialog-title";

export function QuestionDeleteDialog({
  title,
  onConfirm,
  onCancel,
  disabled = false,
}: QuestionDeleteDialogProps) {
  // Focus lands on the safe action, not the destructive one: this dialog
  // confirms a delete, so a stray Enter must not be the one that deletes.
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  function handleCancel() {
    if (!disabled) onCancel();
  }

  return (
    <ModalDialog
      titleId={TITLE_ID}
      onCancel={handleCancel}
      initialFocusRef={cancelButtonRef}
      dismissOnBackdrop
    >
      <h2 id={TITLE_ID} className="font-bold text-lg text-on-surface">
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
          className="rounded-lg bg-surface-container-high px-md py-sm font-medium text-on-surface text-sm transition-colors hover:bg-surface-container-highest disabled:cursor-not-allowed disabled:opacity-50"
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
    </ModalDialog>
  );
}
