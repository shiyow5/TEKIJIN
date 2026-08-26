"use client";

/**
 * Hero question bar (#392): the same question form `/questions` shows, on the
 * hub, submitting the exact same way — via {@link useAskQuestion} — so pressing
 * it here does what pressing it there does: generate a session id, POST /ask, and
 * go straight to `/session/{id}`. `/questions` is no longer part of this path.
 *
 * The form itself is {@link QuestionForm} (#421); this component contributes only
 * the hub's own centring wrapper. It used to carry its own copy of the markup,
 * which drifted from `/questions` within a day (#411).
 */

import { QuestionForm } from "@/components/QuestionForm";

export function HeroQuestionBar() {
  return (
    <div className="mt-md flex w-full max-w-3xl flex-col items-center text-center">
      <QuestionForm />
    </div>
  );
}
