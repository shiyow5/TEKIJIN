import { ProcessingScreen } from "@/components/ProcessingScreen";

/**
 * Session route (product-spec 画面2 / F-08) — reached after POST /ask succeeds.
 *
 * Thin server wrapper: it resolves the route param and hands the id to the
 * client `ProcessingScreen`, which subscribes to GET /events/{id} and renders
 * the live thinking-progress. The header/main landmark come from app/layout.tsx.
 */
export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ProcessingScreen sessionId={id} />;
}
