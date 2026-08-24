import { QuestionHistoryScreen } from "@/components/QuestionHistoryScreen";

/**
 * Full question-history route (#208/#F9).
 *
 * Reached from "最近のあなたの質問"'s "すべて見る" link. The screen itself is a
 * client component (state + GET/DELETE /questions); this route file is a thin
 * server wrapper, matching `app/questions/page.tsx`'s convention.
 */
export default function QuestionHistoryPage() {
  return <QuestionHistoryScreen />;
}
