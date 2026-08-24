"use client";

/**
 * Popup shown after "この内容で依頼する" is pressed, letting the asker pick how
 * the responder should be consulted before the draft is actually sent
 * (POST /handoff/draft carries the choice as `consult_method`).
 */

import { useEffect, useRef } from "react";
import type { ConsultMethod } from "@/lib/api-types";

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
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstButtonRef = useRef<HTMLButtonElement>(null);

  // `aria-modal="true"` promises assistive tech that the rest of the page is
  // inert, so the focus contract has to hold: move focus in on open, keep Tab
  // inside, and hand it back to whatever opened us on close. Without this, Tab
  // walked straight out of the dialog into the header nav.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    firstButtonRef.current?.focus();
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-on-surface/40 p-md">
      {/* biome-ignore lint/a11y/useSemanticElements: role="dialog" + aria-modal
      matches this component's own onCancel/Escape handling; the native
      <dialog> element's imperative showModal()/close() API isn't needed here. */}
      <div
        role="dialog"
        ref={dialogRef}
        aria-modal="true"
        aria-labelledby="consult-method-dialog-title"
        className="flex w-full max-w-sm flex-col gap-md rounded-xl border border-outline-variant bg-surface-container-lowest p-lg shadow-md"
      >
        <h2 id="consult-method-dialog-title" className="font-bold text-lg text-on-surface">
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
      </div>
    </div>
  );
}
