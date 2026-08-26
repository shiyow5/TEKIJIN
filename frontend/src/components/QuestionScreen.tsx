"use client";

/**
 * Question screen (product-spec 画面1 / F-01).
 *
 * A single free-text input — the AI classifies topic/category, so the user never
 * tags or picks a category (product-spec). Submission is handled by
 * {@link useAskQuestion}, shared with the hub's own hero bar (#392) so both
 * screens generate/reuse the session id and recover from a 409 the same way.
 * The header/user display is provided globally by `AppHeader` (app/layout.tsx).
 */

import { PageBackLink } from "@/components/PageBackLink";
import { RecentQuestions } from "@/components/RecentQuestions";
import { useAskQuestion } from "@/hooks/useAskQuestion";
import type { FormEvent } from "react";

export interface QuestionScreenProps {
  /**
   * Optional hook called with the created session id after a successful /ask,
   * instead of the default router navigation. Lets a parent own the transition
   * and keeps the component testable without a router.
   */
  onSubmitted?: (sessionId: string) => void;
}

export function QuestionScreen({ onSubmitted }: QuestionScreenProps) {
  const { question, setQuestion, submitting, error, canSubmit, submit } =
    useAskQuestion(onSubmitted);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submit();
  }

  return (
    <div className="flex w-full flex-col items-center">
      <div className="w-full max-w-4xl">
        <PageBackLink href="/" label="ホームへ戻る" />
      </div>
      <section className="mt-lg mb-margin flex w-full max-w-3xl flex-col items-center text-center">
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
            className="mt-lg inline-flex min-h-[48px] items-center justify-center gap-sm rounded-full bg-primary px-lg py-sm font-bold text-on-primary text-lg shadow-md transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "送信中..." : "聞いてみる"}
          </button>
        </form>

        {error ? (
          <p role="alert" className="mt-md text-error text-sm">
            {error}
          </p>
        ) : null}
      </section>

      <div className="w-full max-w-4xl">
        <RecentQuestions />
      </div>
    </div>
  );
}
