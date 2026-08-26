"use client";

/**
 * The question form itself — heading, input, submit, error (#421).
 *
 * Shared by the hub's {@link HeroQuestionBar} and `/questions`'s
 * {@link QuestionScreen}, which previously kept two copies of this markup. #392
 * had already extracted the LOGIC into {@link useAskQuestion}; the appearance
 * stayed duplicated, and drifted within a day — #411 fixed a heading that had
 * become 24px on one screen and 30px on the other.
 *
 * The unit test that was supposed to catch that drift was called "mirrors the
 * /questions heading" but compared only the WORDS, so it passed while the sizes
 * disagreed. One component removes the class of bug rather than guarding it: the
 * two screens can no longer differ because there is nothing left to differ.
 *
 * Callers keep their own outer wrapper (the hub centres it; `/questions` puts
 * `RecentQuestions` underneath), which is the only thing that was ever meant to
 * differ between them.
 */

import { useAskQuestion } from "@/hooks/useAskQuestion";
import type { FormEvent } from "react";

export interface QuestionFormProps {
  /**
   * Optional hook called with the created session id after a successful /ask,
   * instead of the default router navigation. Lets a parent own the transition
   * and keeps the component testable without a router.
   */
  onSubmitted?: (sessionId: string) => void;
}

export function QuestionForm({ onSubmitted }: QuestionFormProps) {
  const { question, setQuestion, submitting, error, canSubmit, submit } =
    useAskQuestion(onSubmitted);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit();
  }

  return (
    <>
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
    </>
  );
}
