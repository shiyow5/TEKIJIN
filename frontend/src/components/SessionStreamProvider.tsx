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
import { type ReactNode, createContext, useContext } from "react";

const SessionStreamContext = createContext<EventStreamState | null>(null);

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
  const live = useEventStream(sessionId, {
    enabled: streamState === undefined,
    eventSourceFactory,
    baseUrl,
  });
  const value = streamState ?? live;
  return <SessionStreamContext.Provider value={value}>{children}</SessionStreamContext.Provider>;
}

/** Read the session stream, or `null` when rendered outside a provider. */
export function useOptionalSessionStream(): EventStreamState | null {
  return useContext(SessionStreamContext);
}

/** Read the session stream; throws if used outside a {@link SessionStreamProvider}. */
export function useSessionStream(): EventStreamState {
  const value = useContext(SessionStreamContext);
  if (value === null) {
    throw new Error("useSessionStream must be used within a SessionStreamProvider");
  }
  return value;
}
