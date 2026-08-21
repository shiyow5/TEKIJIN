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
    },
  },
  plugins: [],
};

export default config;
