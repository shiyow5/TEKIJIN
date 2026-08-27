"use client";

/**
 * Confirmation modal for marking a still-pending past question self-resolved
 * (#159). Extracted from the former `QuestionResolveButton` (#397, since
 * removed as its only caller — `HistoryScreen` — moved to
 * `HistoryRowOptionsMenu`'s "…" menu) so the same confirmation copy/markup
 * isn't duplicated across triggers.
 *
 * The dialog chrome (overlay, role/aria, Tab-trap, Escape, focus restore) comes
 * from `ModalDialog`, matching `QuestionDeleteDialog`'s split.
 */

import { ModalDialog } from "@/components/ModalDialog";
import { useRef } from "react";

export interface QuestionResolveDialogProps {
  title: string;
  onConfirm: () => void;
  onCancel: () => void;
  disabled?: boolean;
}

const TITLE_ID = "question-resolve-dialog-title";

export function QuestionResolveDialog({
  title,
  onConfirm,
  onCancel,
  disabled = false,
}: QuestionResolveDialogProps) {
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  function handleCancel() {
    if (!disabled) onCancel();
  }

  return (
    <ModalDialog titleId={TITLE_ID} onCancel={handleCancel} initialFocusRef={confirmButtonRef}>
      <h2 id={TITLE_ID} className="font-bold text-lg text-on-surface">
        自分で解決しましたか？
      </h2>
      <p className="text-on-surface-variant text-sm">「{title}」を解決済みとして記録します。</p>
      <div className="flex justify-end gap-sm">
        <button
          type="button"
          disabled={disabled}
          onClick={onCancel}
          className="rounded-md bg-surface-container-high px-md py-sm font-medium text-on-surface text-sm transition-colors hover:bg-surface-container-highest disabled:cursor-not-allowed disabled:opacity-50"
        >
          やめる
        </button>
        <button
          ref={confirmButtonRef}
          type="button"
          disabled={disabled}
          onClick={onConfirm}
          className="rounded-md bg-primary px-md py-sm font-bold text-on-primary text-sm shadow-sm transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          解決済みにする
        </button>
      </div>
    </ModalDialog>
  );
}
