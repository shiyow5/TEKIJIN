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
    render(<ConfidenceGauge percent={100} />);
    // role=img label carries the level AND the concrete percent — nothing depends on color.
    expect(screen.getByRole("img", { name: "適合度 100%（高）" })).toBeInTheDocument();
    // The concrete number is shown inside the ring (円の中に数値, #適合度).
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("shows the level's matching number inside the ring (中=66, 低=33)", () => {
    setReducedMotion(true);
    const { rerender } = render(<ConfidenceGauge percent={66} />);
    expect(screen.getByText("66")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "適合度 66%（中）" })).toBeInTheDocument();
    rerender(<ConfidenceGauge percent={33} />);
    expect(screen.getByText("33")).toBeInTheDocument();
  });

  it("keeps a medium fit and high evidence confidence visibly separate (#540)", () => {
    setReducedMotion(true);
    render(<ConfidenceGauge percent={47} />);
    expect(screen.getByRole("img", { name: "適合度 47%（中）" })).toBeInTheDocument();
    expect(screen.getByText("47")).toBeInTheDocument();
  });

  it("animates the ring on mount when motion is allowed", async () => {
    setReducedMotion(false);
    render(<ConfidenceGauge percent={66} />);
    await waitFor(() => expect(animateMock).toHaveBeenCalledTimes(1));
  });

  it("does not animate under prefers-reduced-motion", async () => {
    setReducedMotion(true);
    render(<ConfidenceGauge percent={33} />);
    // Give the (skipped) effect a tick; the animation must never fire.
    await new Promise((r) => setTimeout(r, 20));
    expect(animateMock).not.toHaveBeenCalled();
    expect(screen.getByRole("img", { name: "適合度 33%（低）" })).toBeInTheDocument();
  });

  it("renders a neutral gauge (50) for an unknown level without crashing", () => {
    setReducedMotion(true);
    render(<ConfidenceGauge />);
    expect(screen.getByRole("img", { name: "適合度 50%（中）" })).toBeInTheDocument();
    expect(screen.getByText("50")).toBeInTheDocument();
  });

  it("falls back to the final ring if anime.js fails to load", async () => {
    setReducedMotion(false);
    animateMock.mockImplementation(() => {
      throw new Error("boom");
    });
    render(<ConfidenceGauge percent={100} />);
    // 高 == full ring -> final strokeDashoffset 0; the .catch fallback applies it.
    await waitFor(() => {
      const arc = document.querySelector("circle[stroke-linecap='round']") as SVGCircleElement;
      expect(arc.style.strokeDashoffset).toBe("0");
    });
  });
});
