"use client";

import { getRecentQuestions } from "@/lib/api-client";
import type { RecentQuestionItem } from "@/lib/api-types";
import { type Dispatch, type SetStateAction, useEffect, useRef, useState } from "react";

export type RecentQuestionsPhase = "loading" | "ready" | "error";

export interface RecentQuestionsState {
  phase: RecentQuestionsPhase;
  items?: RecentQuestionItem[];
}

/**
 * Fetch the acting user's recent questions with **revalidate-on-focus** (#468).
 *
 * Both the home panel (#125) and the full history screen (#208) need the same
 * behaviour: after a run completes on `/session/[id]/result` and the user navigates
 * back, the App Router serves this cached subtree WITHOUT remounting, so a
 * mount-only fetch would keep showing a stale list that omits the just-asked
 * question. Refetching when the page regains focus/visibility fixes that (the API
 * already returns pending questions). Shared here so the logic — and its test —
 * live once instead of being copy-pasted into both screens.
 *
 * Returns `[state, setState]` so callers keep their own optimistic updates
 * (delete/resolve patch the list locally without a refetch).
 *
 * Overlapping triggers (alt-tab fires `visibilitychange` + `focus`; a bfcache
 * restore adds `pageshow`) can start several refetches at once, so a monotonic
 * request id guards against an older response landing after a newer one and
 * flipping the list back to stale data.
 */
export function useRecentQuestions(
  currentUserId: string | null,
  options: { limit?: number } = {},
): [RecentQuestionsState, Dispatch<SetStateAction<RecentQuestionsState>>] {
  const { limit } = options;
  const [state, setState] = useState<RecentQuestionsState>({ phase: "loading" });
  const seqRef = useRef(0);

  useEffect(() => {
    if (currentUserId === null) {
      setState({ phase: "loading" });
      return;
    }
    let active = true;
    const load = (initial: boolean) => {
      if (initial) setState({ phase: "loading" });
      const seq = ++seqRef.current;
      const request =
        limit === undefined
          ? getRecentQuestions(currentUserId)
          : getRecentQuestions(currentUserId, { limit });
      request
        .then((items) => {
          // Ignore a response superseded by a newer load (out-of-order landing).
          if (active && seq === seqRef.current) setState({ phase: "ready", items });
        })
        .catch(() => {
          if (active && seq === seqRef.current) {
            // Keep a stale-but-good list on a BACKGROUND refetch failure; only the
            // very first (loading) fetch surfaces the error screen.
            setState((prev) => (prev.phase === "ready" ? prev : { phase: "error" }));
          }
        });
    };
    const revalidate = () => {
      if (document.visibilityState === "visible") load(false);
    };
    load(true);
    window.addEventListener("focus", revalidate);
    window.addEventListener("pageshow", revalidate);
    document.addEventListener("visibilitychange", revalidate);
    return () => {
      active = false;
      window.removeEventListener("focus", revalidate);
      window.removeEventListener("pageshow", revalidate);
      document.removeEventListener("visibilitychange", revalidate);
    };
  }, [currentUserId, limit]);

  return [state, setState];
}
