import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    // Pin the document origin. `window.localStorage` throws `SecurityError` on an
    // opaque origin (jsdom's own default is `about:blank`), and three test files
    // clear storage in `beforeEach` — so an opaque origin fails all 18 of their
    // tests at once, with an error that points at the test rather than the cause.
    // Vitest's default happens to be this same URL; setting it explicitly means
    // the suite no longer depends on that default holding.
    environmentOptions: { jsdom: { url: "http://localhost:3000" } },
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}", "src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**"],
      thresholds: {
        lines: 90,
        functions: 90,
        statements: 90,
        branches: 90,
      },
    },
  },
});
