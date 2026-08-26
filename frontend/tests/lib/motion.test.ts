import { REVEAL_CLASS, revealDelayMs, revealStyle } from "@/lib/motion";
import { describe, expect, it } from "vitest";

describe("motion reveal helpers", () => {
  it("gives the first item no delay so it appears immediately", () => {
    expect(revealDelayMs(0)).toBe(0);
    expect(revealDelayMs(-1)).toBe(0);
  });

  it("staggers later items by a fixed step", () => {
    expect(revealDelayMs(1)).toBe(70);
    expect(revealDelayMs(2)).toBe(140);
    expect(revealDelayMs(1)).toBeLessThan(revealDelayMs(2));
  });

  it("caps the delay so a long list's tail does not wait unreasonably", () => {
    // 10 * 70 = 700 would be too long; capped at 420.
    expect(revealDelayMs(10)).toBe(420);
    expect(revealDelayMs(100)).toBe(420);
  });

  it("honours a custom step and cap", () => {
    expect(revealDelayMs(3, 50, 1000)).toBe(150);
    expect(revealDelayMs(3, 50, 100)).toBe(100);
  });

  it("renders an inline animation-delay style for the given index", () => {
    expect(revealStyle(0)).toEqual({ animationDelay: "0ms" });
    expect(revealStyle(2)).toEqual({ animationDelay: "140ms" });
  });

  it("disables the animation under reduced motion via a utility class", () => {
    // The reveal must not depend on JS timing: it is a CSS animation that is
    // switched off (not merely instant) when the user prefers reduced motion.
    expect(REVEAL_CLASS).toContain("animate-reveal");
    expect(REVEAL_CLASS).toContain("motion-reduce:animate-none");
  });
});
