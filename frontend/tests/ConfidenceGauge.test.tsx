import { ConfidenceGauge } from "@/components/result/ConfidenceGauge";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock anime.js so the test never depends on rAF timing; capture the call so we
// can assert the animation is wired (and skipped under reduced motion).
const animateMock = vi.fn();
vi.mock("animejs", () => ({ animate: (...args: unknown[]) => animateMock(...args) }));

function setReducedMotion(reduced: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reduced && query.includes("reduce"),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
    onchange: null,
  }));
}

beforeEach(() => animateMock.mockReset());
afterEach(() => vi.restoreAllMocks());

describe("ConfidenceGauge", () => {
  it("exposes the qualitative level as an accessible label and visible text", () => {
    setReducedMotion(false);
    render(<ConfidenceGauge level="高" />);
    // role=img with the same label the card showed as text — nothing depends on color.
    expect(screen.getByRole("img", { name: "適合度 高" })).toBeInTheDocument();
    expect(screen.getByText("高")).toBeInTheDocument();
    // never surfaces a raw percentage.
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("animates the ring on mount when motion is allowed", async () => {
    setReducedMotion(false);
    render(<ConfidenceGauge level="中" />);
    await waitFor(() => expect(animateMock).toHaveBeenCalledTimes(1));
  });

  it("does not animate under prefers-reduced-motion", async () => {
    setReducedMotion(true);
    render(<ConfidenceGauge level="低" />);
    // Give the (skipped) effect a tick; the animation must never fire.
    await new Promise((r) => setTimeout(r, 20));
    expect(animateMock).not.toHaveBeenCalled();
    expect(screen.getByRole("img", { name: "適合度 低" })).toBeInTheDocument();
  });

  it("renders a neutral gauge for an unknown level without crashing", () => {
    setReducedMotion(true);
    render(<ConfidenceGauge level="不明" />);
    expect(screen.getByRole("img", { name: "適合度 不明" })).toBeInTheDocument();
  });
});
