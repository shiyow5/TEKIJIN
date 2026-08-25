import { AnswerScreen } from "@/components/AnswerScreen";
import { PageBackLink } from "@/components/PageBackLink";

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
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col py-lg">
      <PageBackLink href="/inbox" label="受信箱へ戻る" />
      <AnswerScreen sessionId={session_id} />
    </div>
  );
}
