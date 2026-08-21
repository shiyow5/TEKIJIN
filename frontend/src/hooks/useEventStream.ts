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
 * so tests can drive a fake — jsdom has no `EventSource`.
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

const PARSE_ERROR = "ストリームの解析に失敗しました。";
const STREAM_ERROR = "処理中にエラーが発生しました。";
const CONNECTION_ERROR = "接続に問題が発生しました。";

const INITIAL_STATE: EventStreamState = { events: [], terminal: false };

/** Fold one received event into the state, immutably. */
function reduceEvent(prev: EventStreamState, name: SseEventName, data: unknown): EventStreamState {
  const events = [...prev.events, { event: name, data } as StreamEvent];
  switch (name) {
    case "understood":
      return { ...prev, events, understood: data as UnderstoodData };
    case "route":
      return { ...prev, events, route: data as RouteData };
    case "recommend":
      return { ...prev, events, recommend: data as RecommendData };
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
  const { baseUrl, eventSourceFactory, enabled = true } = options;
  const [state, setState] = useState<EventStreamState>(INITIAL_STATE);

  useEffect(() => {
    if (!enabled || !sessionId) {
      return;
    }

    // Fresh subscription: reset any prior session's accumulated state.
    setState(INITIAL_STATE);

    const base = baseUrl ?? getApiBaseUrl();
    const url = `${base}/events/${encodeURIComponent(sessionId)}`;
    const source = eventSourceFactory ? eventSourceFactory(url) : new globalThis.EventSource(url);

    let closed = false;
    const close = () => {
      if (!closed) {
        closed = true;
        source.close();
      }
    };

    const onEvent = (event: MessageEvent) => {
      const name = event.type as SseEventName;
      let data: unknown;
      try {
        data = JSON.parse(event.data);
      } catch {
        setState((prev) => ({ ...prev, error: PARSE_ERROR }));
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
      // A native connection error. Ignore it after a normal terminal close (the
      // browser fires onerror when the server ends the stream) or if an error is
      // already shown; the browser retries transient failures on its own.
      setState((prev) =>
        prev.terminal || prev.error ? prev : { ...prev, error: CONNECTION_ERROR },
      );
    };

    return () => {
      for (const name of EVENT_NAMES) {
        source.removeEventListener(name, onEvent as EventListener);
      }
      close();
    };
  }, [sessionId, baseUrl, eventSourceFactory, enabled]);

  return state;
}
