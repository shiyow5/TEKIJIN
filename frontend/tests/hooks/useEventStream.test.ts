import { useEventStream } from "@/hooks/useEventStream";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

/**
 * Minimal fake EventSource — jsdom has none. Models the real behaviour the hook
 * relies on: named server events carry string `data`; a native transport error
 * carries NO data and is dispatched to both `onerror` and any `"error"`
 * listener; `readyState` tracks CONNECTING(0)/OPEN(1)/CLOSED(2); `close()`
 * moves it to CLOSED.
 */
class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  url: string;
  readyState = FakeEventSource.OPEN;
  closed = false;
  onerror: ((ev: Event) => void) | null = null;
  private listeners: Record<string, ((ev: Event) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, cb: EventListener) {
    const bucket = this.listeners[type] ?? [];
    bucket.push(cb as unknown as (ev: Event) => void);
    this.listeners[type] = bucket;
  }

  removeEventListener(type: string, cb: EventListener) {
    this.listeners[type] = (this.listeners[type] ?? []).filter(
      (fn) => fn !== (cb as unknown as (ev: Event) => void),
    );
  }

  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }

  /** Dispatch a named server event with JSON string data. */
  emit(type: string, data: unknown) {
    const event = { type, data: JSON.stringify(data) } as MessageEvent;
    for (const cb of this.listeners[type] ?? []) {
      cb(event);
    }
  }

  /** Dispatch a named server event with an arbitrary (possibly invalid) body. */
  emitRaw(type: string, raw: string) {
    const event = { type, data: raw } as MessageEvent;
    for (const cb of this.listeners[type] ?? []) {
      cb(event);
    }
  }

  /**
   * Dispatch a native transport error: a plain Event (no `data`) delivered to
   * `onerror` and the `"error"` listener, with `readyState` set to CONNECTING
   * (browser is retrying) or CLOSED (stream is really dead).
   */
  nativeError(readyState: number) {
    this.readyState = readyState;
    const event = new Event("error");
    this.onerror?.(event);
    for (const cb of this.listeners.error ?? []) {
      cb(event);
    }
  }
}

function setup(sessionId = "abc-123") {
  let created: FakeEventSource | undefined;
  const factory = (url: string) => {
    created = new FakeEventSource(url);
    return created as unknown as EventSource;
  };
  const view = renderHook(() =>
    useEventStream(sessionId, { baseUrl: "http://api.test", eventSourceFactory: factory }),
  );
  // biome-ignore lint/style/noNonNullAssertion: factory runs synchronously in the effect.
  return { view, source: () => created! };
}

describe("useEventStream", () => {
  it("builds the /events/{id} URL from the base and session id", () => {
    const { source } = setup("sess_1");
    expect(source().url).toBe("http://api.test/events/sess_1");
  });

  it("accumulates events in order with the latest payload of each kind", () => {
    const { view, source } = setup();

    act(() =>
      source().emit("understood", {
        topics: ["ネットワーク"],
        products: ["UTM"],
        situation: "移行",
        question_type: "how",
        confidence: 0.9,
      }),
    );
    act(() =>
      source().emit("route", { route: "person", reason: "詳しい人がいる", confidence: 0.8 }),
    );
    act(() =>
      source().emit("recommend", {
        recommendations: [
          { person_id: "E001", name: "高梨", score: 0.9, confidence: "high", reasons: [] },
        ],
      }),
    );
    act(() => source().emit("draft", { draft: "依頼文です" }));

    expect(view.result.current.events.map((e) => e.event)).toEqual([
      "understood",
      "route",
      "recommend",
      "draft",
    ]);
    expect(view.result.current.understood?.confidence).toBe(0.9);
    expect(view.result.current.route?.route).toBe("person");
    expect(view.result.current.recommend?.recommendations).toHaveLength(1);
    expect(view.result.current.draft?.draft).toBe("依頼文です");
    expect(view.result.current.terminal).toBe(false);
    expect(source().closed).toBe(false);
  });

  it("marks terminal and closes on a done event", () => {
    const { view, source } = setup();
    act(() => source().emit("done", { status: "sent" }));

    expect(view.result.current.terminal).toBe(true);
    expect(view.result.current.done?.status).toBe("sent");
    expect(source().closed).toBe(true);
  });

  it("marks terminal and closes on a message event", () => {
    const { view, source } = setup();
    act(() => source().emit("message", { status: "off_topic", message: "業務外です" }));

    expect(view.result.current.terminal).toBe(true);
    expect(view.result.current.message?.message).toBe("業務外です");
    expect(source().closed).toBe(true);
  });

  it("records a followup without terminating", () => {
    const { view, source } = setup();
    act(() => source().emit("followup", { question: "製品名は？", missing: ["product"] }));

    expect(view.result.current.followup?.question).toBe("製品名は？");
    expect(view.result.current.terminal).toBe(false);
    expect(source().closed).toBe(false);
  });

  it("ignores a replayed identical followup (same question and missing)", () => {
    const { view, source } = setup();
    act(() => source().emit("followup", { question: "製品名は？", missing: ["product"] }));
    act(() => source().emit("followup", { question: "製品名は？", missing: ["product"] }));

    // Only one followup event is recorded — the reconnect replay is dropped.
    expect(view.result.current.events.filter((e) => e.event === "followup")).toHaveLength(1);
  });

  it("records a genuinely different followup", () => {
    const { view, source } = setup();
    act(() => source().emit("followup", { question: "製品名は？", missing: ["product"] }));
    act(() => source().emit("followup", { question: "拠点数は？", missing: ["sites"] }));

    expect(view.result.current.followup?.question).toBe("拠点数は？");
    expect(view.result.current.events.filter((e) => e.event === "followup")).toHaveLength(2);
  });

  it("ignores a replayed identical draft and keeps the stream open (send interrupt)", () => {
    const { view, source } = setup();
    act(() => source().emit("draft", { draft: "高梨さんへの依頼文" }));
    // The backend re-emits the same draft on every reconnect while paused at
    // `send`; the replay must not append a second event, and the stream must
    // stay open so this consumer can still advance the graph to `done`.
    act(() => source().emit("draft", { draft: "高梨さんへの依頼文" }));

    expect(view.result.current.events.filter((e) => e.event === "draft")).toHaveLength(1);
    expect(view.result.current.draft?.draft).toBe("高梨さんへの依頼文");
    expect(view.result.current.terminal).toBe(false);
    expect(source().closed).toBe(false);
  });

  it("does not close or error on a reconnect after a draft (send-interrupt pause)", () => {
    const { view, source } = setup();
    act(() => source().emit("draft", { draft: "依頼文" }));
    // A transient reconnect (CONNECTING) at the send pause must be ignored — the
    // consumer stays open to receive the eventual `done`.
    act(() => source().nativeError(FakeEventSource.CONNECTING));

    expect(source().closed).toBe(false);
    expect(view.result.current.error).toBeUndefined();
  });

  it("ignores a replayed identical recommend and keeps the draft (send reconnect)", () => {
    const { view, source } = setup();
    const rec = {
      recommendations: [
        { person_id: "E001", name: "高梨", score: 0.9, confidence: "高", reasons: [] },
      ],
    };
    act(() => source().emit("recommend", rec));
    act(() => source().emit("draft", { draft: "高梨さんへの依頼文" }));
    // A send-interrupt reconnect now replays recommend THEN draft; an identical
    // recommend must not append a second event nor clear the standing draft.
    act(() => source().emit("recommend", rec));

    expect(view.result.current.events.filter((e) => e.event === "recommend")).toHaveLength(1);
    expect(view.result.current.draft?.draft).toBe("高梨さんへの依頼文");
  });

  it("clears the previous draft when a new recommendation arrives (reroute)", () => {
    const { view, source } = setup();
    act(() => source().emit("recommend", { recommendations: [{ person_id: "E001" }] }));
    act(() => source().emit("draft", { draft: "旧候補への依頼文" }));
    act(() => source().emit("recommend", { recommendations: [{ person_id: "E002" }] }));

    // The stale draft is dropped until the replacement draft arrives.
    expect(view.result.current.draft).toBeUndefined();
    expect(view.result.current.recommend?.recommendations[0].person_id).toBe("E002");
  });

  it("surfaces a generic error and closes on a server error event (has data)", () => {
    const { view, source } = setup();
    act(() => source().emit("error", { error: "boom" }));

    expect(view.result.current.error).toBeTruthy();
    expect(view.result.current.error).not.toContain("boom");
    expect(source().closed).toBe(true);
  });

  it("ignores a malformed event without latching an error or closing", () => {
    const { view, source } = setup();
    act(() => source().emitRaw("understood", "not-json"));

    expect(view.result.current.error).toBeUndefined();
    expect(view.result.current.understood).toBeUndefined();
    expect(view.result.current.terminal).toBe(false);
    expect(source().closed).toBe(false);
  });

  it("does not error on a native transport error while reconnecting (CONNECTING)", () => {
    const { view, source } = setup();
    act(() => source().nativeError(FakeEventSource.CONNECTING));
    expect(view.result.current.error).toBeUndefined();
  });

  it("sets a connection error when a native error leaves the stream CLOSED", () => {
    const { view, source } = setup();
    act(() => source().nativeError(FakeEventSource.CLOSED));
    expect(view.result.current.error).toBeTruthy();
  });

  it("ignores a native error after a terminal event (normal server close)", () => {
    const { view, source } = setup();
    act(() => source().emit("done", { status: "sent" }));
    act(() => source().nativeError(FakeEventSource.CLOSED));

    expect(view.result.current.terminal).toBe(true);
    expect(view.result.current.error).toBeUndefined();
  });

  it("closes the connection on unmount (cleanup)", () => {
    const { view, source } = setup();
    const es = source();
    view.unmount();
    expect(es.closed).toBe(true);
  });

  it("does not subscribe when disabled", () => {
    let created = false;
    renderHook(() =>
      useEventStream("abc", {
        enabled: false,
        eventSourceFactory: () => {
          created = true;
          return {} as EventSource;
        },
      }),
    );
    expect(created).toBe(false);
  });
});
