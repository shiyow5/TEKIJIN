import { HistoryScreen } from "@/components/HistoryScreen";

/**
 * 質問履歴 route (#208) — the full, all-time list of the acting user's questions
 * and the place to delete old ones (#207). Reached from the header nav. The screen
 * is a client component (state + GET /questions?limit); this is a thin wrapper.
 */
export default function HistoryPage() {
  return <HistoryScreen />;
}
