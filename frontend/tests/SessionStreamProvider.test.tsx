import {
  SessionStreamProvider,
  useOptionalSessionStream,
  useSessionStream,
} from "@/components/SessionStreamProvider";
import type { EventStreamState } from "@/hooks/useEventStream";
import { render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

function state(partial: Partial<EventStreamState>): EventStreamState {
  return { events: [], terminal: false, ...partial };
}

function Consumer() {
  const stream = useSessionStream();
  return <div data-testid="route">{stream.route?.route ?? "none"}</div>;
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
});
