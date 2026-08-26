import { KnowledgeScreen } from "@/components/KnowledgeScreen";

/**
 * ナレッジ route (#293, #301) — the company-wide, searchable list of questions a
 * person has resolved. Reached from the header nav. The screen is a client
 * component (search/filter state + GET /knowledge); this is a thin wrapper.
 */
export default function KnowledgePage() {
  return <KnowledgeScreen />;
}
