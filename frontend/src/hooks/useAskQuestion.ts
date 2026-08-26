"use client";

/**
 * Shared "ask a question" submit flow: generates a session id, POSTs /ask,
 * and hands off to the processing screen at `/session/{id}` (#36). Extracted
 * from `QuestionScreen` when the hub's own hero bar (#392) needed to submit
 * directly too, instead of redirecting through `/questions` first — the two
 * screens would otherwise carry two copies of the same session-id-reuse /
 * 409-recovery logic.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import { ApiError, postAsk } from "@/lib/api-client";
import { createSessionId } from "@/lib/session";
import { useRouter } from "next/navigation";
import { useState } from "react";

const SUBMIT_ERROR_MESSAGE = "質問の送信に失敗しました。時間をおいて再度お試しください。";

export interface UseAskQuestionResult {
  question: string;
  setQuestion: (value: string) => void;
  submitting: boolean;
  error: string | null;
  canSubmit: boolean;
  submit: () => Promise<void>;
}

/**
 * @param onSubmitted Optional hook called with the created session id after a
 * successful /ask, instead of the default router navigation. Lets a caller
 * own the transition and keeps it testable without a router.
 */
export function useAskQuestion(onSubmitted?: (sessionId: string) => void): UseAskQuestionResult {
  const router = useRouter();
  // The acting user (chosen in the header switcher) is the asker. Until the
  // directory resolves, `currentUserId` is null and submit stays gated.
  const { currentUserId } = useCurrentUser();
  const [question, setQuestionState] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Session id for the *current* question. Reused across retries so an
  // ambiguous failure (the backend persisted /ask but the ack was lost) does
  // not create a second session on retry; cleared when the question changes.
  const [pendingSessionId, setPendingSessionId] = useState<string | null>(null);

  const trimmed = question.trim();
  const canSubmit = trimmed.length > 0 && !submitting && currentUserId !== null;

  function setQuestion(value: string) {
    setQuestionState(value);
    setPendingSessionId(null);
  }

  async function submit() {
    if (!canSubmit || currentUserId === null) {
      return;
    }

    setSubmitting(true);
    setError(null);
    const sessionId = pendingSessionId ?? createSessionId();
    setPendingSessionId(sessionId);

    const proceed = () => {
      if (onSubmitted) {
        onSubmitted(sessionId);
      } else {
        router.push(`/session/${sessionId}`);
      }
    };

    try {
      await postAsk({ asker_id: currentUserId, question: trimmed, session_id: sessionId });
      proceed();
    } catch (err) {
      // A 409 on a retry means the original /ask was actually accepted (this
      // session already has a run in flight) — recover by watching it rather
      // than re-showing the error and getting stuck.
      if (err instanceof ApiError && err.status === 409) {
        proceed();
        return;
      }
      // Other transport / non-2xx failures surface the same user-facing message;
      // the detail is not leaked to the UI.
      setError(SUBMIT_ERROR_MESSAGE);
      setSubmitting(false);
    }
  }

  return { question, setQuestion, submitting, error, canSubmit, submit };
}
