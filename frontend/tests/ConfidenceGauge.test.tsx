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
  it("exposes the level + percent as an accessible label and shows both", () => {
    setReducedMotion(false);
    render(<ConfidenceGauge level="高" />);
    // role=img label carries the level AND the concrete percent — nothing depends on color.
    expect(screen.getByRole("img", { name: "適合度 高（100%）" })).toBeInTheDocument();
    expect(screen.getByText("高")).toBeInTheDocument();
    // The concrete number is shown inside the ring (円の中に数値, #適合度).
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("shows the level's matching number inside the ring (中=66, 低=33)", () => {
    setReducedMotion(true);
    const { rerender } = render(<ConfidenceGauge level="中" />);
    expect(screen.getByText("66")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "適合度 中（66%）" })).toBeInTheDocument();
    rerender(<ConfidenceGauge level="低" />);
    expect(screen.getByText("33")).toBeInTheDocument();
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
    expect(screen.getByRole("img", { name: "適合度 低（33%）" })).toBeInTheDocument();
  });

  it("renders a neutral gauge (50) for an unknown level without crashing", () => {
    setReducedMotion(true);
    render(<ConfidenceGauge level="不明" />);
    expect(screen.getByRole("img", { name: "適合度 不明（50%）" })).toBeInTheDocument();
    expect(screen.getByText("50")).toBeInTheDocument();
  });

  it("uses the continuous fitScore for the ring/number when provided (#205)", () => {
    setReducedMotion(true);
    const { rerender } = render(<ConfidenceGauge level="中" fitScore={0.9} />);
    expect(screen.getByText("90")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "適合度 中（90%）" })).toBeInTheDocument();

    // Same discrete label ("中"), different fitScore -> visibly different
    // number/fill — this is exactly the "looks identical every time" bug fix.
    rerender(<ConfidenceGauge level="中" fitScore={0.3} />);
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.queryByText("66")).not.toBeInTheDocument();
  });

  it("clamps an out-of-range fitScore into 0..1", () => {
    setReducedMotion(true);
    const { rerender } = render(<ConfidenceGauge level="高" fitScore={1.5} />);
    expect(screen.getByText("100")).toBeInTheDocument();
    rerender(<ConfidenceGauge level="低" fitScore={-0.2} />);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("falls back to the discrete per-level percent when fitScore is omitted", () => {
    setReducedMotion(true);
    render(<ConfidenceGauge level="中" />);
    expect(screen.getByText("66")).toBeInTheDocument();
  });

  it("falls back to the final ring if anime.js fails to load", async () => {
    setReducedMotion(false);
    animateMock.mockImplementation(() => {
      throw new Error("boom");
    });
    render(<ConfidenceGauge level="高" />);
    // 高 == full ring -> final strokeDashoffset 0; the .catch fallback applies it.
    await waitFor(() => {
      const arc = document.querySelector("circle[stroke-linecap='round']") as SVGCircleElement;
      expect(arc.style.strokeDashoffset).toBe("0");
    });
  });
});
