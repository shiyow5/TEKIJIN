"use client";

/**
 * Hero question bar (#392): mirrors `/questions`'s own heading, input, and
 * "聞いてみる" button, and submits the exact same way — via {@link
 * useAskQuestion} — so pressing it here does the same thing pressing it on
 * `/questions` does: generate a session id, POST /ask, and go straight to
 * `/session/{id}`. `/questions` is no longer part of this path at all.
 *
 * Renders its own heading as the page's `<h1>` — the hub dropped the separate
 * "TEKIJIN" title + description, so this is now the top of the page's
 * heading hierarchy, not a second-level heading under it.
 */

import { useAskQuestion } from "@/hooks/useAskQuestion";
import type { FormEvent } from "react";

export function HeroQuestionBar() {
  const { question, setQuestion, submitting, error, canSubmit, submit } = useAskQuestion();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit();
  }

  return (
    <div className="mt-md flex w-full max-w-3xl flex-col items-center text-center">
      {/* Same heading typography as `/questions` — the hero is meant to read as
          the same thing in a different place, so the size and the gap below it
          have to match, not just the words (they drifted at 2xl/mb-md). */}
      <h1 className="mb-margin font-bold text-3xl text-on-surface tracking-tight">
        何を知りたいですか？
      </h1>
      <form onSubmit={handleSubmit} className="w-full">
        <div className="flex w-full items-center gap-xs rounded-xl border border-outline-variant bg-surface-container-lowest px-sm py-xs shadow-sm focus-within:border-primary">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            aria-label="質問を入力"
            placeholder="質問を入力してください..."
            className="w-full bg-transparent px-sm py-2 text-on-surface outline-none placeholder:text-on-surface-variant"
          />
        </div>

        <button
          type="submit"
          disabled={!canSubmit}
          className="mt-lg inline-flex min-h-[48px] items-center justify-center gap-sm rounded-full bg-primary px-lg py-sm font-bold text-lg text-on-primary shadow-md transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "送信中..." : "聞いてみる"}
        </button>
      </form>

      {error ? (
        <p role="alert" className="mt-md text-error text-sm">
          {error}
        </p>
      ) : null}
    </div>
  );
}
