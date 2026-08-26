import {
  SessionStreamProvider,
  useOptionalSessionId,
  useOptionalSessionStream,
  useOptionalSessionStreamRestart,
  useSessionStream,
} from "@/components/SessionStreamProvider";
import type { EventStreamState } from "@/hooks/useEventStream";
import { fireEvent, render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

function state(partial: Partial<EventStreamState>): EventStreamState {
  return { events: [], terminal: false, ...partial };
}

function Consumer() {
  const stream = useSessionStream();
  return <div data-testid="route">{stream.route?.route ?? "none"}</div>;
}

function RestartConsumer() {
  const restart = useOptionalSessionStreamRestart();
  return (
    <button type="button" onClick={() => restart?.()}>
      再接続
    </button>
  );
}

describe("SessionStreamProvider", () => {
  it("provides the injected stream state to consumers", () => {
    render(
      <SessionStreamProvider
        sessionId="abc-123"
        streamState={state({ route: { route: "person", reason: "", confidence: 0.9 } })}
      >
        <Consumer />
      </SessionStreamProvider>,
    );
    expect(screen.getByTestId("route")).toHaveTextContent("person");
  });

  it("useSessionStream throws when used outside a provider", () => {
    expect(() => renderHook(() => useSessionStream())).toThrow(/within a SessionStreamProvider/);
  });

  it("useOptionalSessionStream returns null outside a provider", () => {
    const { result } = renderHook(() => useOptionalSessionStream());
    expect(result.current).toBeNull();
  });

  it("exposes the session id via useOptionalSessionId (null outside a provider)", () => {
    expect(renderHook(() => useOptionalSessionId()).result.current).toBeNull();

    const wrapper = ({ children }: { children: ReactNode }) => (
      <SessionStreamProvider sessionId="sess-42" streamState={state({})}>
        {children}
      </SessionStreamProvider>
    );
    expect(renderHook(() => useOptionalSessionId(), { wrapper }).result.current).toBe("sess-42");
  });

  it("opens exactly one EventSource for the live subscription", () => {
    const urls: string[] = [];
    const factory = (url: string) => {
      urls.push(url);
      return {
        addEventListener: () => {},
        removeEventListener: () => {},
        close: () => {},
        onerror: null,
        readyState: 1,
      } as unknown as EventSource;
    };
    const wrapper = ({ children }: { children: ReactNode }) => (
      <SessionStreamProvider
        sessionId="abc-123"
        baseUrl="http://api.test"
        eventSourceFactory={factory}
      >
        {children}
      </SessionStreamProvider>
    );
    renderHook(() => useOptionalSessionStream(), { wrapper });
    expect(urls).toEqual(["http://api.test/events/abc-123"]);
  });

  it("reopens the shared stream when a consumer requests a restart", () => {
    const sources: Array<{ close: ReturnType<typeof vi.fn> }> = [];
    const factory = () => {
      const source = {
        addEventListener: () => {},
        removeEventListener: () => {},
        close: vi.fn(),
        onerror: null,
        readyState: 1,
      };
      sources.push(source);
      return source as unknown as EventSource;
    };
    render(
      <SessionStreamProvider sessionId="abc-123" eventSourceFactory={factory}>
        <RestartConsumer />
      </SessionStreamProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "再接続" }));

    expect(sources).toHaveLength(2);
    expect(sources[0].close).toHaveBeenCalledOnce();
  });
});
