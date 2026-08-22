"use client";

/**
 * Responder-facing handoff state (product-spec 画面4).
 *
 * Loads GET /handoff/{session_id} on mount, then lets the responder act via one
 * of three choices — all resolved to a POST /answer outcome:
 *
 *   answer (回答する)      -> outcome "accepted"
 *   defer  (今は難しい)    -> outcome "declined"  (F-09: the asker is rerouted)
 *   refer  (別の人を薦める) -> outcome "declined"  (interim; a dedicated backend
 *                                                 transition is tracked in #76)
 *
 * A 404/409 on load means the session is no longer awaiting a responder (already
 * answered, expired, or awaiting a clarification instead) — surfaced as "gone"
 * so the screen shows a terminal message rather than a broken form. A failed
 * submit is retryable: it returns to the ready phase with an inline error.
 */

import { ApiError, getHandoff, postAnswer } from "@/lib/api-client";
import type { HandoffResponse, Outcome } from "@/lib/api-types";
import { useCallback, useEffect, useState } from "react";

export type HandoffAction = "answer" | "defer" | "refer";
export type HandoffPhase = "loading" | "error" | "ready" | "submitting" | "done";
export type HandoffErrorKind = "load" | "gone";

const OUTCOME_BY_ACTION: Record<HandoffAction, Outcome> = {
  answer: "accepted",
  defer: "declined",
  refer: "declined",
};

const SUBMIT_ERROR_MESSAGE = "送信に失敗しました。時間をおいて再度お試しください。";

export interface HandoffState {
  phase: HandoffPhase;
  handoff?: HandoffResponse;
  /** The completed action, once `phase === "done"`. */
  action?: HandoffAction;
  /** Why loading failed, when `phase === "error"`. */
  errorKind?: HandoffErrorKind;
  /** Inline, retryable error from a failed outcome submit. */
  submitError?: string;
}

export interface UseHandoffResult extends HandoffState {
  submit: (action: HandoffAction) => void;
}

export function useHandoff(sessionId: string): UseHandoffResult {
  const [state, setState] = useState<HandoffState>({ phase: "loading" });

  useEffect(() => {
    let active = true;
    setState({ phase: "loading" });
    getHandoff(sessionId)
      .then((handoff) => {
        if (active) setState({ phase: "ready", handoff });
      })
      .catch((err: unknown) => {
        if (!active) return;
        // 404 (no handoff) / 409 (awaiting a clarification) == nothing to answer.
        const gone = err instanceof ApiError && (err.status === 404 || err.status === 409);
        setState({ phase: "error", errorKind: gone ? "gone" : "load" });
      });
    return () => {
      active = false;
    };
  }, [sessionId]);

  const submit = useCallback(
    (action: HandoffAction) => {
      setState((prev) => {
        if (prev.phase !== "ready") return prev; // ignore double-submit
        return { ...prev, phase: "submitting", submitError: undefined };
      });
      postAnswer({ session_id: sessionId, outcome: OUTCOME_BY_ACTION[action] })
        .then(() => {
          setState((prev) => ({ ...prev, phase: "done", action }));
        })
        .catch(() => {
          // Retryable: back to ready with an inline error (never dead-ends).
          setState((prev) => ({ ...prev, phase: "ready", submitError: SUBMIT_ERROR_MESSAGE }));
        });
    },
    [sessionId],
  );

  return { ...state, submit };
}
