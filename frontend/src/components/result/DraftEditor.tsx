"use client";

/**
 * Editable "聞き方の下書き" for the main-line result. The AI-generated draft
 * seeds a local, editable textarea; "この方に送る" hands the current text to the
 * parent, which confirms the send (UI-only; the responder's accept/decline is
 *画面4 / #38).
 */

import { useState } from "react";

export interface DraftEditorProps {
  initialDraft: string;
  disabled?: boolean;
  onSend: (text: string) => void;
}

export function DraftEditor({ initialDraft, disabled = false, onSend }: DraftEditorProps) {
  const [text, setText] = useState(initialDraft);
  const canSend = text.trim().length > 0 && !disabled;

  return (
    <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-md shadow-sm">
      <h3 className="mb-sm font-bold text-lg text-on-surface">聞き方の下書き</h3>
      <textarea
        aria-label="聞き方の下書き"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="質問の背景や詳細を記載してください..."
        className="h-32 w-full resize-none rounded-lg border border-outline-variant bg-surface p-sm text-on-surface outline-none focus:border-primary"
      />
      <div className="mt-md flex justify-end">
        <button
          type="button"
          disabled={!canSend}
          onClick={() => onSend(text.trim())}
          className="inline-flex min-h-[48px] items-center gap-sm rounded-lg bg-primary px-lg py-3 font-bold text-on-primary shadow-sm transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          この方に送る
        </button>
      </div>
    </div>
  );
}
