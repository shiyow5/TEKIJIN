"use client";

/**
 * Polls one chat thread's history and lets the acting user send messages (#224).
 *
 * Mirrors {@link useChatThreads}' polling/visibility behaviour but scoped to a
 * single `threadId`, with a shorter default interval — an open conversation
 * should feel closer to live than the thread list. Sending resets the poll
 * timer and immediately refetches, so the sender's own message (and any race
 * with the counterpart's) appears without waiting out the interval.
 */

import { getChatThread, postMessage } from "@/lib/api-client";
import type { ChatThreadDetail } from "@/lib/api-types";
import { useCallback, useEffect, useRef, useState } from "react";

export type ChatThreadPhase = "idle" | "loading" | "ready" | "error";

export interface UseChatThreadOptions {
  /** Poll interval while the tab is visible, in ms. */
  intervalMs?: number;
  enabled?: boolean;
}

export interface UseChatThreadResult {
  phase: ChatThreadPhase;
  detail?: ChatThreadDetail;
  sending: boolean;
  sendError?: string;
  send: (body: string) => void;
}

const DEFAULT_INTERVAL_MS = 4_000;
const SEND_ERROR_MESSAGE = "送信に失敗しました。時間をおいて再度お試しください。";

export function useChatThread(
  threadId: number | null,
  employeeId: string | null,
  options: UseChatThreadOptions = {},
): UseChatThreadResult {
  const { intervalMs = DEFAULT_INTERVAL_MS, enabled = true } = options;
  const [phase, setPhase] = useState<ChatThreadPhase>("idle");
  const [detail, setDetail] = useState<ChatThreadDetail | undefined>(undefined);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | undefined>(undefined);
  const loadedOnce = useRef(false);
  // The active poll cycle's refetch — exposed to `send` via a ref so a fresh
  // message can force an immediate refetch without re-running the whole effect.
  const refetchNow = useRef<() => void>(() => {});

  useEffect(() => {
    loadedOnce.current = false;
    setSendError(undefined);
    if (!enabled || threadId === null || employeeId === null) {
      setPhase("idle");
      setDetail(undefined);
      refetchNow.current = () => {};
      return;
    }

    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;

    const tick = (immediate = false) => {
      if (!immediate) clearTimeout(timer);
      controller?.abort();
      controller = new AbortController();
      getChatThread(threadId, employeeId, { signal: controller.signal })
        .then((body) => {
          if (!active) return;
          loadedOnce.current = true;
          setDetail(body);
          setPhase("ready");
        })
        .catch(() => {
          if (!active) return;
          if (!loadedOnce.current) setPhase("error");
        })
        .finally(() => {
          if (active && document.visibilityState === "visible") {
            timer = setTimeout(() => tick(), intervalMs);
          }
        });
    };

    refetchNow.current = () => tick(true);

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        tick(true);
      } else {
        clearTimeout(timer);
      }
    };

    setPhase("loading");
    tick(true);
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      refetchNow.current = () => {};
    };
  }, [threadId, employeeId, enabled, intervalMs]);

  const send = useCallback(
    (body: string) => {
      const trimmed = body.trim();
      if (threadId === null || employeeId === null || trimmed === "" || sending) return;
      setSending(true);
      setSendError(undefined);
      postMessage({ thread_id: threadId, sender_id: employeeId, body: trimmed })
        .then(() => {
          refetchNow.current();
        })
        .catch(() => {
          setSendError(SEND_ERROR_MESSAGE);
        })
        .finally(() => {
          setSending(false);
        });
    },
    [threadId, employeeId, sending],
  );

  return { phase, detail, sending, sendError, send };
}
