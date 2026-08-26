"use client";

/**
 * Subscribe to the server-sent event stream for a session (GET /events/{id}).
 *
 * The backend emits named events (see `backend/src/tekijin/api/events.py`):
 * understood / followup / route / recommend / draft / done / message / error.
 * This hook opens an `EventSource`, accumulates each event (ordered list plus
 * the latest payload of each kind) with immutable updates, and closes the
 * connection when a terminal event (done / message / error) arrives.
 *
 * The `EventSource` constructor is injectable via `options.eventSourceFactory`
 * so tests can drive a fake — jsdom has no `EventSource`. `eventSourceFactory`
 * and `baseUrl` are effect dependencies: pass reference-stable values (module
 * constants or memoized) so the subscription is not torn down every render.
 */

import type {
  DoneData,
  DraftData,
  ErrorData,
  FollowupData,
  MessageData,
  RecommendData,
  RouteData,
  SseEventName,
  UnderstoodData,
} from "@/lib/api-types";
import { getAuthToken } from "@/lib/auth-token";
import { getApiBaseUrl } from "@/lib/config";
import { useEffect, useState } from "react";

/** One received SSE event, tagged by name (discriminated union). */
export type StreamEvent =
  | { event: "understood"; data: UnderstoodData }
  | { event: "followup"; data: FollowupData }
  | { event: "route"; data: RouteData }
  | { event: "recommend"; data: RecommendData }
  | { event: "draft"; data: DraftData }
  | { event: "done"; data: DoneData }
  | { event: "message"; data: MessageData }
  | { event: "error"; data: ErrorData };

export interface EventStreamState {
  /** Events in the order received. */
  events: StreamEvent[];
  understood?: UnderstoodData;
  route?: RouteData;
  recommend?: RecommendData;
  draft?: DraftData;
  followup?: FollowupData;
  message?: MessageData;
  done?: DoneData;
  /** True once a terminal event (done / message) has been received. */
  terminal: boolean;
  /** User-facing error text (generic — no server detail leaked). */
  error?: string;
}

export interface UseEventStreamOptions {
  /** Override the resolved API base URL. */
  baseUrl?: string;
  /** Inject the `EventSource` constructor (required in tests). */
  eventSourceFactory?: (url: string) => EventSource;
  /** Subscribe only when true (default true). */
  enabled?: boolean;
  /** Change to intentionally discard terminal state and subscribe again. */
  restartKey?: number;
}

const EVENT_NAMES: readonly SseEventName[] = [
  "understood",
  "followup",
  "route",
  "recommend",
  "draft",
  "done",
  "message",
  "error",
] as const;

const STREAM_ERROR = "処理中にエラーが発生しました。";
const CONNECTION_ERROR = "接続に問題が発生しました。";

// WHATWG EventSource.readyState values (0 CONNECTING, 1 OPEN, 2 CLOSED). Kept as
// a literal so the hook does not depend on a global `EventSource` (absent in
// jsdom / SSR).
const READY_STATE_CLOSED = 2;

const INITIAL_STATE: EventStreamState = { events: [], terminal: false };

/** Two followups are the same when their question and missing list match. */
function isSameFollowup(a: FollowupData | undefined, b: FollowupData): boolean {
  if (!a || a.question !== b.question) {
    return false;
  }
  const am = a.missing ?? [];
  const bm = b.missing ?? [];
  return am.length === bm.length && am.every((v, i) => v === bm[i]);
}

/** Two recommend payloads are the same when their candidate ids match in order. */
function isSameRecommend(a: RecommendData | undefined, b: RecommendData): boolean {
  if (!a) {
    return false;
  }
  const ar = a.recommendations;
  const br = b.recommendations;
  return ar.length === br.length && ar.every((r, i) => r.person_id === br[i].person_id);
}

/** Fold one received event into the state, immutably. */
function reduceEvent(prev: EventStreamState, name: SseEventName, data: unknown): EventStreamState {
  // The backend re-emits the pending followup on every automatic reconnect while
  // the session stays paused; drop an identical replay so the UI does not treat
  // it as a new question and reopen an already-answered form.
  if (name === "followup" && isSameFollowup(prev.followup, data as FollowupData)) {
    return prev;
  }
  // A session paused at `send` now re-emits the candidates AND the draft on every
  // reconnect (backend `reconnect_events`). Drop an identical `recommend` replay
  // so the reconnect stays silent — otherwise it would append an event and clear
  // the draft on every reconnect, also defeating the identical-draft check below.
  if (name === "recommend" && isSameRecommend(prev.recommend, data as RecommendData)) {
    return prev;
  }
  // A session paused at the `send` interrupt re-emits the same `draft` on every
  // reconnect (see backend `reconnect_event`). Drop an identical replay so the
  // reconnect stays silent — but the connection is deliberately kept open (see
  // `onerror`) so this consumer can still advance the graph and receive the
  // eventual `done` once the responder submits an outcome (#38).
  if (name === "draft" && prev.draft?.draft === (data as DraftData).draft) {
    return prev;
  }
  const events = [...prev.events, { event: name, data } as StreamEvent];
  switch (name) {
    case "understood":
      return { ...prev, events, understood: data as UnderstoodData };
    case "route":
      return { ...prev, events, route: data as RouteData };
    case "recommend":
      // A fresh recommendation (including a reroute after a decline) invalidates
      // the previous candidate's draft; the following draft event resets it.
      return { ...prev, events, recommend: data as RecommendData, draft: undefined };
    case "draft":
      return { ...prev, events, draft: data as DraftData };
    case "followup":
      return { ...prev, events, followup: data as FollowupData };
    case "done":
      return { ...prev, events, done: data as DoneData, terminal: true };
    case "message":
      return { ...prev, events, message: data as MessageData, terminal: true };
    case "error":
      return { ...prev, events, error: STREAM_ERROR };
    default:
      return { ...prev, events };
  }
}

export function useEventStream(
  sessionId: string,
  options: UseEventStreamOptions = {},
): EventStreamState {
  const { baseUrl, eventSourceFactory, enabled = true, restartKey = 0 } = options;
  const [state, setState] = useState<EventStreamState>(INITIAL_STATE);

  // biome-ignore lint/correctness/useExhaustiveDependencies: restartKey intentionally reopens the same session after an explicit transition.
  useEffect(() => {
    if (!enabled || !sessionId) {
      return;
    }

    // Fresh subscription: reset any prior session's accumulated state.
    setState(INITIAL_STATE);

    const base = baseUrl ?? getApiBaseUrl();
    // A browser EventSource cannot send an Authorization header, so the access
    // token rides as a ?token= query parameter (the backend accepts it there for
    // /events only). Read at subscribe time — the token is stable within a session.
    const token = getAuthToken();
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    const url = `${base}/events/${encodeURIComponent(sessionId)}${query}`;
    const source = eventSourceFactory ? eventSourceFactory(url) : new globalThis.EventSource(url);

    let closed = false;
    const close = () => {
      if (!closed) {
        closed = true;
        source.close();
      }
    };

    const onEvent = (event: MessageEvent) => {
      // A native transport `error` also dispatches to the "error" listener but
      // carries no string `data`; let it fall through to `onerror` instead of
      // trying to parse `undefined`.
      if (typeof event.data !== "string") {
        return;
      }
      const name = event.type as SseEventName;
      let data: unknown;
      try {
        data = JSON.parse(event.data);
      } catch {
        // Malformed payload for a single event: skip it. Do not latch an error
        // banner or close — a later well-formed event still renders.
        return;
      }
      setState((prev) => reduceEvent(prev, name, data));
      if (name === "done" || name === "message" || name === "error") {
        close();
      }
    };

    for (const name of EVENT_NAMES) {
      source.addEventListener(name, onEvent as EventListener);
    }

    source.onerror = () => {
      // Native connection error. The browser auto-reconnects transient failures
      // (readyState CONNECTING) — the backend legitimately ends the HTTP segment
      // at a followup OR a send interrupt and re-emits the pending event on the
      // next reconnect (deduped in `reduceEvent`), so that is NOT fatal. Keeping
      // the connection alive is required: the queued responder outcome is only
      // consumed by an active /events reader, so this consumer must stay open to
      // advance the graph and receive `done`. Only a genuinely CLOSED stream is
      // surfaced. Ignore once terminal, or when an error is already shown.
      setState((prev) => {
        if (prev.terminal || prev.error) {
          return prev;
        }
        if (source.readyState === READY_STATE_CLOSED) {
          return { ...prev, error: CONNECTION_ERROR };
        }
        return prev; // CONNECTING: transient reconnect, not fatal
      });
    };

    return () => {
      for (const name of EVENT_NAMES) {
        source.removeEventListener(name, onEvent as EventListener);
      }
      close();
    };
  }, [sessionId, baseUrl, eventSourceFactory, enabled, restartKey]);

  return state;
}
