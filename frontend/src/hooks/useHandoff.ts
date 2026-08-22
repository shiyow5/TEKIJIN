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

import { advanceSession, ApiError, getHandoff, postAnswer } from "@/lib/api-client";
import type { HandoffResponse, Outcome } from "@/lib/api-types";
import { useCallback, useEffect, useRef, useState } from "react";

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
  // Single-flight guard: prevents a rapid double-tap from firing a second
  // postAnswer (the second would 409 and could clobber the successful state).
  const inFlight = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

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
      if (inFlight.current) return; // gate the POST itself, not just the state
      inFlight.current = true;
      setState((prev) =>
        prev.phase === "ready" ? { ...prev, phase: "submitting", submitError: undefined } : prev,
      );

      // The outcome is recorded synchronously by POST /answer, but the graph only
      // advances (accept -> C8 done, decline -> reroute) when an /events reader
      // consumes the queued resume. Drive it here so the hand-off completes even
      // if the asker's tab is closed. Best-effort: the outcome is already durable.
      const finish = () => {
        advanceSession(sessionId).finally(() => {
          inFlight.current = false;
          if (mounted.current) setState((prev) => ({ ...prev, phase: "done", action }));
        });
      };

      postAnswer({ session_id: sessionId, outcome: OUTCOME_BY_ACTION[action] })
        .then(finish)
        .catch((err: unknown) => {
          // Ambiguous-ack recovery: a 409 means the resume was already queued /
          // the run already advanced — treat it as success (mirrors the ask flow),
          // so a lost acknowledgement never strands the responder on the form.
          if (err instanceof ApiError && err.status === 409) {
            finish();
            return;
          }
          inFlight.current = false;
          if (mounted.current) {
            setState((prev) => ({ ...prev, phase: "ready", submitError: SUBMIT_ERROR_MESSAGE }));
          }
        });
    },
    [sessionId],
  );

  return { ...state, submit };
}
