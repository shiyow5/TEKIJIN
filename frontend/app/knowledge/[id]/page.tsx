import { KnowledgeDetailScreen } from "@/components/KnowledgeDetailScreen";

/**
 * Knowledge detail route (#293, #301) — the `kind="qa"` counterpart to
 * `/documents/[id]` (#143). Thin server wrapper: resolves the source id from
 * the path and hands it to the client `KnowledgeDetailScreen`, which loads
 * GET /knowledge/{id} and renders the full question + answer.
 */
export default async function KnowledgeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <KnowledgeDetailScreen sourceId={id} />;
}
