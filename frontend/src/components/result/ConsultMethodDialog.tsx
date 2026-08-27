"use client";

/**
 * Popup shown after "この内容で依頼する" is pressed, letting the asker pick how
 * the responder should be consulted before the draft is actually sent
 * (POST /handoff/draft carries the choice as `consult_method`).
 */

import { ModalDialog } from "@/components/ModalDialog";
import type { ConsultMethod } from "@/lib/api-types";
import { useId, useRef } from "react";

export interface ConsultMethodDialogProps {
  onChoose: (consultMethod: ConsultMethod) => void;
  onCancel: () => void;
  disabled?: boolean;
}

export function ConsultMethodDialog({
  onChoose,
  onCancel,
  disabled = false,
}: ConsultMethodDialogProps) {
  const firstButtonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  return (
    <ModalDialog
      titleId={titleId}
      onCancel={disabled ? () => {} : onCancel}
      initialFocusRef={firstButtonRef}
    >
      <h2 id={titleId} className="font-bold text-lg text-on-surface">
        相談方法を選んでください
      </h2>
      <p className="text-on-surface-variant text-sm">この依頼をどちらの方法で相談しますか。</p>
      <div className="flex flex-col gap-sm">
        <button
          ref={firstButtonRef}
          type="button"
          disabled={disabled}
          onClick={() => onChoose("chat")}
          className="min-h-[48px] rounded-lg bg-primary px-lg py-sm font-bold text-on-primary shadow-sm transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          チャットで相談する
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onChoose("direct")}
          className="min-h-[48px] rounded-lg border border-primary px-lg py-sm font-bold text-primary shadow-sm transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          直接相談する
        </button>
      </div>
      <button
        type="button"
        disabled={disabled}
        onClick={onCancel}
        className="self-center text-on-surface-variant text-sm underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:opacity-50"
      >
        キャンセル
      </button>
    </ModalDialog>
  );
}
