/**
 * Session route (product-spec 画面2 / F-08) — reached after POST /ask succeeds.
 *
 * #35 lands a minimal placeholder so a successful submission does not 404 while
 * the demo flow is wired end to end. #36 replaces the body with the live
 * processing / thinking-progress screen that subscribes to GET /events/{id}.
 */
export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <main className="mx-auto flex max-w-content flex-col gap-md px-gutter py-lg">
      <h1 className="text-2xl font-bold text-on-surface">処理中…</h1>
      <p className="text-on-surface-variant">質問を受け付けました。詳しい方を探しています。</p>
      <p className="text-sm text-on-surface-variant" data-testid="session-id">
        セッション: {id}
      </p>
    </main>
  );
}
