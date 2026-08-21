/**
 * Design tokens for TEKIJIN (たずねーる).
 *
 * Source of truth: ui_template/stitch_tekijin_ai_ui_dashboard/tekijin/DESIGN.md
 * These constants are consumed by tailwind.config.ts and are unit-tested so the
 * theme stays in sync with the design system. Keep values immutable.
 */

export const colors = {
  surface: "#f9f9ff",
  "surface-dim": "#cfdaf1",
  "surface-bright": "#f9f9ff",
  "surface-container-lowest": "#ffffff",
  "surface-container-low": "#f0f3ff",
  "surface-container": "#e7eeff",
  "surface-container-high": "#dee8ff",
  "surface-container-highest": "#d8e3fa",
  "on-surface": "#111c2c",
  "on-surface-variant": "#454652",
  "inverse-surface": "#263142",
  "inverse-on-surface": "#ebf1ff",
  outline: "#757684",
  "outline-variant": "#c5c5d4",
  "surface-tint": "#4355b9",
  primary: "#24389c",
  "on-primary": "#ffffff",
  "primary-container": "#3f51b5",
  "on-primary-container": "#cacfff",
  "inverse-primary": "#bac3ff",
  secondary: "#006a6a",
  "on-secondary": "#ffffff",
  "secondary-container": "#90efef",
  "on-secondary-container": "#006e6e",
  tertiary: "#6c3400",
  "on-tertiary": "#ffffff",
  "tertiary-container": "#8f4700",
  "on-tertiary-container": "#ffc7a2",
  error: "#ba1a1a",
  "on-error": "#ffffff",
  "error-container": "#ffdad6",
  "on-error-container": "#93000a",
  background: "#f9f9ff",
  "on-background": "#111c2c",
  "surface-variant": "#d8e3fa",
} as const;

export const borderRadius = {
  sm: "0.25rem",
  DEFAULT: "0.5rem",
  md: "0.75rem",
  lg: "1rem",
  xl: "1.5rem",
  full: "9999px",
} as const;

/** 8px-grid spacing scale from DESIGN.md. */
export const spacing = {
  base: "8px",
  xs: "4px",
  sm: "12px",
  md: "24px",
  lg: "40px",
  gutter: "24px",
  margin: "32px",
} as const;

export const fontFamily = {
  sans: ["var(--font-noto)", "Noto Sans JP", "sans-serif"],
} as const;

/** Max content width for the fixed-fluid hybrid layout (desktop). */
export const layout = {
  maxWidth: "1440px",
  bodyLineHeight: "1.7",
} as const;

export type ColorToken = keyof typeof colors;

export const designTokens = {
  colors,
  borderRadius,
  spacing,
  fontFamily,
  layout,
} as const;

export default designTokens;
