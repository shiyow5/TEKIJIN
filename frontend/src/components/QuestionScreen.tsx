"use client";

/**
 * Question screen (product-spec 画面1 / F-01).
 *
 * A single free-text input — the AI classifies topic/category, so the user never
 * tags or picks a category (product-spec). The form is {@link QuestionForm},
 * shared with the hub's hero bar (#421); this screen adds the back link and the
 * "最近のあなたの質問" panel beneath it. The header/user display is provided
 * globally by `AppHeader` (app/layout.tsx).
 */

import { PageBackLink } from "@/components/PageBackLink";
import { QuestionForm } from "@/components/QuestionForm";
import { RecentQuestions } from "@/components/RecentQuestions";

export interface QuestionScreenProps {
  /**
   * Optional hook called with the created session id after a successful /ask,
   * instead of the default router navigation. Lets a parent own the transition
   * and keeps the component testable without a router.
   */
  onSubmitted?: (sessionId: string) => void;
}

export function QuestionScreen({ onSubmitted }: QuestionScreenProps) {
  return (
    <div className="flex w-full flex-col items-center">
      <div className="w-full max-w-4xl">
        <PageBackLink href="/" label="ホームへ戻る" />
      </div>
      <section className="mt-lg mb-margin flex w-full max-w-3xl flex-col items-center text-center">
        <QuestionForm onSubmitted={onSubmitted} />
      </section>

      <div className="w-full max-w-4xl">
        <RecentQuestions />
      </div>
    </div>
  );
}
