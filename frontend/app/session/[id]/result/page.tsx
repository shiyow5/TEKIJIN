import { ResultScreen } from "@/components/ResultScreen";

/**
 * Result route (product-spec 画面3). The screen reads the session SSE state from
 * the `SessionStreamProvider` mounted in `app/session/[id]/layout.tsx`, so this
 * route file is a thin client-screen wrapper with no data fetching of its own.
 */
export default function SessionResultPage() {
  return <ResultScreen />;
}
