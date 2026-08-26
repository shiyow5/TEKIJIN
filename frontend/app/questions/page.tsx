import { QuestionScreen } from "@/components/QuestionScreen";

/**
 * Question screen route (product-spec 画面1 / F-01).
 *
 * The hub's own hero bar now submits directly via the same `useAskQuestion`
 * flow (#392), so this route is no longer the primary way to ask — it stays
 * reachable from other entry points (e.g. history, error-recovery links). The
 * screen itself is a client component (state + POST /ask); this route file is
 * a thin server wrapper.
 */
export default function QuestionsPage() {
  return <QuestionScreen />;
}
