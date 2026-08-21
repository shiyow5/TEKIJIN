/**
 * Result route (product-spec 画面3) — reached from the processing screen's
 * "結果を見る" CTA once candidates are found.
 *
 * #36 lands a minimal placeholder so the CTA does not 404 while the flow is
 * wired end to end; #37 replaces the body with the real recommendation result.
 */
export default async function SessionResultPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  // No <main> here: app/layout.tsx already provides the page's main landmark.
  return (
    <div className="mx-auto flex max-w-content flex-col gap-md px-gutter py-lg">
      <h1 className="font-bold text-2xl text-on-surface">結果を準備中…</h1>
      <p className="text-on-surface-variant">回答者候補の詳細を表示します。</p>
      <p className="text-on-surface-variant text-sm" data-testid="session-id">
        セッション: {id}
      </p>
    </div>
  );
}
