import { QuestionScreen } from "@/components/QuestionScreen";

/**
 * Question screen route (product-spec 画面1 / F-01).
 *
 * Reached from the landing hub's "質問する" link. The screen itself is a client
 * component (state + POST /ask); this route file is a thin server wrapper.
 */
export default function QuestionsPage() {
  return <QuestionScreen />;
}
