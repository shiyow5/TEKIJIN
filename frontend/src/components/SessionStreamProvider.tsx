"use client";

/**
 * Session-scoped SSE state shared across the processing and result screens.
 *
 * The route layout (`app/session/[id]/layout.tsx`) mounts this provider once and
 * runs `useEventStream(sessionId)` a single time, so the accumulated
 * recommend/route/draft survive navigation from `/session/[id]` to
 * `/session/[id]/result` (a fresh subscription per screen would lose them).
 *
 * Consumers read the state with `useSessionStream()` (throws outside a provider)
 * or `useOptionalSessionStream()` (returns `null` outside). Test seams:
 * `streamState` injects a fixed state (bypassing the live subscription), and
 * `eventSourceFactory` / `baseUrl` are forwarded to `useEventStream`.
 */

import { type EventStreamState, useEventStream } from "@/hooks/useEventStream";
import { type ReactNode, createContext, useContext, useState } from "react";

const SessionStreamContext = createContext<EventStreamState | null>(null);
// The session id is exposed separately so a screen can POST for this session
// (e.g. confirming the hand-off draft, #174) without threading it through props.
const SessionIdContext = createContext<string | null>(null);
const SessionStreamRestartContext = createContext<(() => void) | null>(null);

export interface SessionStreamProviderProps {
  sessionId: string;
  children: ReactNode;
  /** Test seam: provide a fixed state instead of subscribing. */
  streamState?: EventStreamState;
  /** Test seam: inject the EventSource constructor for the live subscription. */
  eventSourceFactory?: (url: string) => EventSource;
  /** Test seam: override the API base URL for the live subscription. */
  baseUrl?: string;
}

export function SessionStreamProvider({
  sessionId,
  children,
  streamState,
  eventSourceFactory,
  baseUrl,
}: SessionStreamProviderProps) {
  const [restartKey, setRestartKey] = useState(0);
  const live = useEventStream(sessionId, {
    enabled: streamState === undefined,
    eventSourceFactory,
    baseUrl,
    restartKey,
  });
  const value = streamState ?? live;
  return (
    <SessionIdContext.Provider value={sessionId}>
      <SessionStreamRestartContext.Provider value={() => setRestartKey((key) => key + 1)}>
        <SessionStreamContext.Provider value={value}>{children}</SessionStreamContext.Provider>
      </SessionStreamRestartContext.Provider>
    </SessionIdContext.Provider>
  );
}

/** Read the session stream, or `null` when rendered outside a provider. */
export function useOptionalSessionStream(): EventStreamState | null {
  return useContext(SessionStreamContext);
}

/** Read the current session id, or `null` when rendered outside a provider. */
export function useOptionalSessionId(): string | null {
  return useContext(SessionIdContext);
}

/** Restart the shared stream after an explicit server-side state transition. */
export function useOptionalSessionStreamRestart(): (() => void) | null {
  return useContext(SessionStreamRestartContext);
}

/** Read the session stream; throws if used outside a {@link SessionStreamProvider}. */
export function useSessionStream(): EventStreamState {
  const value = useContext(SessionStreamContext);
  if (value === null) {
    throw new Error("useSessionStream must be used within a SessionStreamProvider");
  }
  return value;
}
