import { AnswerScreen } from "@/components/AnswerScreen";

/**
 * Answer route (product-spec 画面4 / the "asked" side). Thin server wrapper: it
 * only resolves the session id from the path and hands it to the client
 * `AnswerScreen`, which loads GET /handoff/{id} and wires the three actions.
 */
export default async function AnswerPage({
  params,
}: {
  params: Promise<{ session_id: string }>;
}) {
  const { session_id } = await params;
  return <AnswerScreen sessionId={session_id} />;
}
