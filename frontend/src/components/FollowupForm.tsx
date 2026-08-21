"use client";

/**
 * Clarification (逆質問) prompt. When the AI needs more information it emits a
 * `followup` event; this renders the question and a simple reply box. On submit
 * the parent POSTs /answer with `{ session_id, reply }` (ResumeRequest) and the
 * event stream resumes over the still-open connection.
 */

import { type FormEvent, useState } from "react";

export interface FollowupFormProps {
  question: string;
  missing?: string[];
  submitting?: boolean;
  errorMessage?: string | null;
  /** Called with the trimmed reply when the user submits. */
  onReply: (reply: string) => void;
}

export function FollowupForm({
  question,
  missing,
  submitting = false,
  errorMessage = null,
  onReply,
}: FollowupFormProps) {
  const [value, setValue] = useState("");
  const trimmed = value.trim();
  const canSubmit = trimmed.length > 0 && !submitting;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    onReply(trimmed);
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full rounded-xl border border-primary-fixed bg-surface-container-low p-md"
    >
      <p className="font-bold text-on-surface">確認させてください</p>
      <p className="mt-xs text-on-surface">{question}</p>

      {missing && missing.length > 0 ? (
        <ul className="mt-sm flex flex-wrap gap-xs">
          {missing.map((item) => (
            <li
              key={item}
              className="rounded-full border border-outline-variant bg-surface-container-lowest px-sm py-[2px] text-on-surface-variant text-xs"
            >
              {item}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-md flex flex-col gap-sm sm:flex-row">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label="補足の回答"
          placeholder="補足を入力してください..."
          className="w-full rounded-lg border border-outline-variant bg-surface-container-lowest px-sm py-2 text-on-surface outline-none focus:border-primary"
        />
        <button
          type="submit"
          disabled={!canSubmit}
          className="min-h-[44px] shrink-0 rounded-full bg-primary px-lg py-sm font-bold text-on-primary transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "送信中..." : "回答する"}
        </button>
      </div>

      {errorMessage ? (
        <p role="alert" className="mt-sm text-error text-sm">
          {errorMessage}
        </p>
      ) : null}
    </form>
  );
}
