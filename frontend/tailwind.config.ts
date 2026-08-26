import type { Config } from "tailwindcss";
import { borderRadius, colors, fontFamily, layout, spacing } from "./src/lib/design-tokens";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}", "./app/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { ...colors },
      borderRadius: { ...borderRadius },
      spacing: { ...spacing },
      fontFamily: {
        sans: [...fontFamily.sans],
      },
      maxWidth: {
        content: layout.maxWidth,
      },
      // Staggered entrance reveal ported from the UX prototype (#475 Screen 01).
      // `both` fill-mode holds the start (opacity 0) through the stagger delay so
      // items fade/slide in one after another. Disabled via `motion-reduce:animate-none`.
      keyframes: {
        reveal: {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        reveal: "reveal 0.45s cubic-bezier(0.2, 0.75, 0.3, 1) both",
      },
    },
  },
  plugins: [],
};

export default config;
