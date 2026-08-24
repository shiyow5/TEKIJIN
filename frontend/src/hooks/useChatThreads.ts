"use client";

/**
 * Polls the accepted-thread chat list for the acting user (#224).
 *
 * Loads GET /messages/threads on mount / whenever `employeeId` changes, then
 * re-polls on an interval so a new thread (the counterpart just accepted) or a
 * new message preview shows up without a manual refresh — no SSE/WebSocket per
 * the issue's scope (polling is sufficient). Polling pauses while the tab is
 * hidden (`document.visibilitychange`) and resumes with an immediate refetch on
 * return, so a backgrounded tab does not keep hammering the API.
 */

import { getChatThreads } from "@/lib/api-client";
import type { ChatThreadSummary } from "@/lib/api-types";
import { useEffect, useRef, useState } from "react";

export type ChatThreadsPhase = "loading" | "ready" | "error";

export interface UseChatThreadsOptions {
  /** Poll interval while the tab is visible, in ms. */
  intervalMs?: number;
  /** Set false to stop polling/fetching entirely (e.g. no acting user yet). */
  enabled?: boolean;
}

export interface UseChatThreadsResult {
  phase: ChatThreadsPhase;
  threads: ChatThreadSummary[];
}

const DEFAULT_INTERVAL_MS = 10_000;

export function useChatThreads(
  employeeId: string | null,
  options: UseChatThreadsOptions = {},
): UseChatThreadsResult {
  const { intervalMs = DEFAULT_INTERVAL_MS, enabled = true } = options;
  const [phase, setPhase] = useState<ChatThreadsPhase>("loading");
  const [threads, setThreads] = useState<ChatThreadSummary[]>([]);
  // Tracks whether ANY fetch has ever succeeded, so a poll failure after a
  // successful load stays quiet (keep showing the last-known list) rather than
  // flashing the whole screen to an error state.
  const loadedOnce = useRef(false);

  useEffect(() => {
    loadedOnce.current = false;
    if (!enabled || employeeId === null) {
      setPhase("loading");
      setThreads([]);
      return;
    }

    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;

    const tick = () => {
      controller?.abort();
      controller = new AbortController();
      getChatThreads(employeeId, { signal: controller.signal })
        .then((items) => {
          if (!active) return;
          loadedOnce.current = true;
          setThreads(items);
          setPhase("ready");
        })
        .catch(() => {
          if (!active) return;
          if (!loadedOnce.current) setPhase("error");
        })
        .finally(() => {
          if (active && document.visibilityState === "visible") {
            timer = setTimeout(tick, intervalMs);
          }
        });
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        clearTimeout(timer);
        tick();
      } else {
        clearTimeout(timer);
      }
    };

    setPhase("loading");
    tick();
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [employeeId, enabled, intervalMs]);

  return { phase, threads };
}
