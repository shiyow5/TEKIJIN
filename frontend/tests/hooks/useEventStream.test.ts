import { useEventStream } from "@/hooks/useEventStream";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

/** Minimal fake EventSource — jsdom has none. */
class FakeEventSource {
  url: string;
  closed = false;
  onerror: ((ev: Event) => void) | null = null;
  private listeners: Record<string, ((ev: MessageEvent) => void)[]> = {};

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, cb: EventListener) {
    const bucket = this.listeners[type] ?? [];
    bucket.push(cb as unknown as (ev: MessageEvent) => void);
    this.listeners[type] = bucket;
  }

  removeEventListener(type: string, cb: EventListener) {
    this.listeners[type] = (this.listeners[type] ?? []).filter(
      (fn) => fn !== (cb as unknown as (ev: MessageEvent) => void),
    );
  }

  close() {
    this.closed = true;
  }

  emit(type: string, data: unknown) {
    const event = { type, data: JSON.stringify(data) } as MessageEvent;
    for (const cb of this.listeners[type] ?? []) {
      cb(event);
    }
  }

  emitRaw(type: string, raw: string) {
    const event = { type, data: raw } as MessageEvent;
    for (const cb of this.listeners[type] ?? []) {
      cb(event);
    }
  }

  triggerError() {
    this.onerror?.(new Event("error"));
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

  it("surfaces a generic error and closes on an error event", () => {
    const { view, source } = setup();
    act(() => source().emit("error", { error: "boom" }));

    expect(view.result.current.error).toBeTruthy();
    expect(view.result.current.error).not.toContain("boom");
    expect(source().closed).toBe(true);
  });

  it("surfaces a parse error when event data is not JSON", () => {
    const { view, source } = setup();
    act(() => source().emitRaw("understood", "not-json"));

    expect(view.result.current.error).toBeTruthy();
    expect(view.result.current.understood).toBeUndefined();
  });

  it("sets a connection error on onerror before any terminal", () => {
    const { view, source } = setup();
    act(() => source().triggerError());
    expect(view.result.current.error).toBeTruthy();
  });

  it("ignores onerror after a terminal event (normal server close)", () => {
    const { view, source } = setup();
    act(() => source().emit("done", { status: "sent" }));
    act(() => source().triggerError());
    // terminal stays, no error overwrite
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
