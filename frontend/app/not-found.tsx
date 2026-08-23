import Link from "next/link";

/**
 * Route-level 404 (#126). Reached for an unknown path (e.g. an old bookmarked
 * link); offers a way home instead of a bare Next.js default.
 */
export default function NotFound() {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col items-center gap-md py-lg text-center">
      <h1 className="font-bold text-2xl text-on-surface">ページが見つかりません</h1>
      <p className="text-on-surface-variant">
        お探しのページは存在しないか、移動した可能性があります。
      </p>
      <Link
        href="/"
        className="min-h-[48px] rounded-full bg-primary px-lg py-sm font-bold text-on-primary shadow-md transition-colors hover:bg-primary-container"
      >
        ホームへ戻る
      </Link>
    </div>
  );
}
