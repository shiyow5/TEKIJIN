"use client";

/**
 * Editable "聞き方の下書き" for the main-line result. The AI-generated draft
 * seeds a local, editable textarea; "この内容で依頼する" hands the current text to
 * the parent, which persists it to the pending hand-off so the responder reads
 * the edited version (POST /handoff/draft — #174; the responder's accept/decline
 * is 画面4 / #38).
 */

import { useEffect, useState } from "react";

export interface DraftEditorProps {
  initialDraft: string;
  disabled?: boolean;
  onSend: (text: string) => void;
}

export function DraftEditor({ initialDraft, disabled = false, onSend }: DraftEditorProps) {
  const [text, setText] = useState(initialDraft);
  const [dirty, setDirty] = useState(false);

  // The draft can arrive over SSE after this editor is already open (the CTA can
  // appear on `recommend`, before `draft`). Sync a later draft in — but never
  // clobber what the user has started editing.
  useEffect(() => {
    if (!dirty) {
      setText(initialDraft);
    }
  }, [initialDraft, dirty]);

  const canSend = text.trim().length > 0 && !disabled;

  function handleChange(value: string) {
    setDirty(true);
    setText(value);
  }

  return (
    <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-md shadow-sm">
      <h2 className="mb-sm font-bold text-lg text-on-surface">聞き方の下書き</h2>
      <textarea
        aria-label="聞き方の下書き"
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        placeholder="この方に伝える依頼内容です。背景や補足を追記・修正できます..."
        className="h-32 w-full resize-none rounded-lg border border-outline-variant bg-surface p-sm text-on-surface outline-none focus:border-primary"
      />
      <div className="mt-md flex justify-end">
        <button
          type="button"
          disabled={!canSend}
          onClick={() => onSend(text.trim())}
          className="inline-flex min-h-[48px] items-center gap-sm rounded-lg bg-primary px-lg py-3 font-bold text-on-primary shadow-sm transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          この内容で依頼する
        </button>
      </div>
    </div>
  );
}
