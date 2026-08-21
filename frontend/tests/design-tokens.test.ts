import {
  borderRadius,
  colors,
  designTokens,
  fontFamily,
  layout,
  spacing,
} from "@/lib/design-tokens";
import { describe, expect, it } from "vitest";

describe("design tokens", () => {
  it("exposes the primary color palette from DESIGN.md", () => {
    expect(colors.primary).toBe("#24389c");
    expect(colors.secondary).toBe("#006a6a");
    expect(colors.surface).toBe("#f9f9ff");
    expect(colors["on-surface"]).toBe("#111c2c");
    expect(colors.outline).toBe("#757684");
    expect(colors.error).toBe("#ba1a1a");
  });

  it("exposes the rounded scale", () => {
    expect(borderRadius.sm).toBe("0.25rem");
    expect(borderRadius.md).toBe("0.75rem");
    expect(borderRadius.lg).toBe("1rem");
    expect(borderRadius.full).toBe("9999px");
  });

  it("exposes the 8px-grid spacing scale", () => {
    expect(spacing.xs).toBe("4px");
    expect(spacing.sm).toBe("12px");
    expect(spacing.md).toBe("24px");
    expect(spacing.lg).toBe("40px");
  });

  it("puts the Noto font variable first in the sans stack", () => {
    expect(fontFamily.sans[0]).toBe("var(--font-noto)");
    expect(fontFamily.sans).toContain("Noto Sans JP");
  });

  it("defines the desktop max content width and body line height", () => {
    expect(layout.maxWidth).toBe("1440px");
    expect(layout.bodyLineHeight).toBe("1.7");
  });

  it("aggregates every token group in the default export", () => {
    expect(designTokens.colors).toBe(colors);
    expect(designTokens.borderRadius).toBe(borderRadius);
    expect(designTokens.spacing).toBe(spacing);
    expect(designTokens.fontFamily).toBe(fontFamily);
    expect(designTokens.layout).toBe(layout);
  });
});
