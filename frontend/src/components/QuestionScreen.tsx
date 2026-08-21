"use client";

/**
 * Question screen (product-spec 画面1 / F-01).
 *
 * A single free-text input — the AI classifies topic/category, so the user never
 * tags or picks a category (product-spec). On submit we generate a session id,
 * POST /ask, and hand off to the processing screen (#36) at `/session/{id}`.
 * The header/user display is provided globally by `AppHeader` (app/layout.tsx).
 */

import { RecentQuestions } from "@/components/RecentQuestions";
import { VoiceInputButton } from "@/components/VoiceInputButton";
import { postAsk } from "@/lib/api-client";
import { createSessionId } from "@/lib/session";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

/**
 * Asker identity. Real user/session wiring lands with later work (#39); for now
 * the foundation uses a fixed employee id. The API accepts a number or "E###".
 */
const ASKER_ID = 1;

const SUBMIT_ERROR_MESSAGE = "質問の送信に失敗しました。時間をおいて再度お試しください。";

export interface QuestionScreenProps {
  /**
   * Optional hook called with the created session id after a successful /ask,
   * instead of the default router navigation. Lets a parent own the transition
   * and keeps the component testable without a router.
   */
  onSubmitted?: (sessionId: string) => void;
}

export function QuestionScreen({ onSubmitted }: QuestionScreenProps) {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Session id for the *current* question. Reused across retries so an
  // ambiguous failure (the backend persisted /ask but the ack was lost) does
  // not create a second session on retry; cleared when the question changes.
  const [pendingSessionId, setPendingSessionId] = useState<string | null>(null);

  const trimmed = question.trim();
  const canSubmit = trimmed.length > 0 && !submitting;

  function handleQuestionChange(value: string) {
    setQuestion(value);
    setPendingSessionId(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    setSubmitting(true);
    setError(null);
    const sessionId = pendingSessionId ?? createSessionId();
    setPendingSessionId(sessionId);

    try {
      await postAsk({ asker_id: ASKER_ID, question: trimmed, session_id: sessionId });
      if (onSubmitted) {
        onSubmitted(sessionId);
      } else {
        router.push(`/session/${sessionId}`);
      }
    } catch {
      // Both transport and non-2xx (ApiError) failures surface the same
      // user-facing message; the detail is not leaked to the UI.
      setError(SUBMIT_ERROR_MESSAGE);
      setSubmitting(false);
    }
  }

  return (
    <div className="flex w-full flex-col items-center">
      <section className="mt-lg mb-margin flex w-full max-w-3xl flex-col items-center text-center">
        <h1 className="mb-margin font-bold text-3xl text-on-surface tracking-tight">
          何を知りたいですか？
        </h1>

        <form onSubmit={handleSubmit} className="w-full">
          <div className="flex w-full items-center gap-xs rounded-xl border border-outline-variant bg-surface-container-lowest px-sm py-xs shadow-sm focus-within:border-primary">
            <input
              type="text"
              value={question}
              onChange={(e) => handleQuestionChange(e.target.value)}
              aria-label="質問を入力"
              placeholder="質問を入力してください..."
              className="w-full bg-transparent px-sm py-2 text-on-surface outline-none placeholder:text-on-surface-variant"
            />
            <VoiceInputButton />
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
