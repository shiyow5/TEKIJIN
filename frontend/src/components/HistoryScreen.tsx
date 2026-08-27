"use client";

/**
 * 質問履歴（全期間の一括管理ビュー, #208）。
 *
 * The question screen's "最近のあなたの質問" panel shows only the newest five (#125);
 * this screen lists ALL of the acting user's past questions, newest first, as the
 * place to review and delete old ones (#207 の削除機能の本格的な置き場所).
 *
 * Each card is a single click target when it has a replayable session (#397):
 * the whole card links to `/session/{id}`, not a small "結果を見る" text — a
 * history-only row (no session) stays non-interactive, unchanged. Delete and
 * self-resolve moved from two always-visible controls into one "…" options
 * menu (`HistoryRowOptionsMenu`), which sits as the card `<li>`'s sibling
 * (not nested in the `Link`) so its clicks never navigate. The list itself is
 * paginated client-side — the full (up to `HISTORY_LIMIT`) set is already
 * fetched in one call, so slicing it locally needs no new backend endpoint.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import { HistoryRowOptionsMenu } from "@/components/HistoryRowOptionsMenu";
import { PageBackLink } from "@/components/PageBackLink";
import { useRecentQuestions } from "@/hooks/useRecentQuestions";
import type { RecentQuestionItem } from "@/lib/api-types";
import { formatDateTimeJst } from "@/lib/datetime";
import Link from "next/link";
import { useEffect, useState } from "react";

/** The full history pulls far more than the 5-item panel; 200 is the API cap. */
const HISTORY_LIMIT = 200;
/** Cards per page (#397). */
const HISTORY_PAGE_SIZE = 5;

/** The resolution line: responder name, self / document self-resolve, or pending. */
function resolutionNote(item: RecentQuestionItem): string {
  if (item.responder_name) return `回答者: ${item.responder_name}`;
  if (item.resolution === "self") return "自分で解決";
  if (item.resolution === "document") return "社内文書で回答";
  return "取り次ぎ先を調整中";
}

function HistoryRow({
  item,
  onDeleted,
  onResolved,
}: {
  item: RecentQuestionItem;
  onDeleted: (questionId: string) => void;
  onResolved: (questionId: string) => void;
}) {
  const date = formatDateTimeJst(item.created_at);
  const body = (
    <>
      <div className="flex items-start justify-between gap-sm pr-8">
        <h3 className="font-bold text-base text-on-surface">{item.title}</h3>
        <span
          className={
            item.resolved
              ? "whitespace-nowrap rounded-full bg-secondary-container px-xs py-[2px] text-on-secondary-container text-xs"
              : "whitespace-nowrap rounded-full border border-outline-variant bg-surface-container-low px-xs py-[2px] text-on-surface-variant text-xs"
          }
        >
          {item.resolved ? "解決済" : "対応中"}
        </span>
      </div>
      <div className="mt-sm flex flex-wrap items-center gap-x-md gap-y-xs text-on-surface-variant text-xs">
        {date ? <span>{date}</span> : null}
        <span>{resolutionNote(item)}</span>
        {item.session_id ? null : <span className="text-on-surface-variant">履歴のみ</span>}
      </div>
    </>
  );

  return (
    <li className="relative rounded-xl border border-outline-variant bg-surface-container-lowest p-md">
      {/* The whole card is the click target when a session is replayable (#397)
          — a small "結果を見る" text link no longer exists. A history-only row
          (no session_id) stays a plain, non-interactive block, unchanged. */}
      {item.session_id ? (
        <Link
          href={`/session/${encodeURIComponent(item.session_id)}/result?from=history`}
          className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          {body}
        </Link>
      ) : (
        <div>{body}</div>
      )}
      <HistoryRowOptionsMenu
        questionId={item.question_id}
        title={item.title}
        pending={item.resolution === "pending"}
        onDeleted={onDeleted}
        onResolved={onResolved}
      />
    </li>
  );
}

export function HistoryScreen() {
  const { currentUserId } = useCurrentUser();
  const [state, setState] = useRecentQuestions(currentUserId, { limit: HISTORY_LIMIT });
  const [page, setPage] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reset only when the acting user changes.
  useEffect(() => {
    setPage(0);
  }, [currentUserId]);

  /** Drop a just-deleted question from the list without a full re-fetch. */
  function handleDeleted(questionId: string) {
    setState((prev) =>
      prev.phase === "ready" && prev.items
        ? { phase: "ready", items: prev.items.filter((i) => i.question_id !== questionId) }
        : prev,
    );
  }

  /** Mark a just-self-resolved question in place (#159) without a full re-fetch. */
  function handleResolved(questionId: string) {
    setState((prev) =>
      prev.phase === "ready" && prev.items
        ? {
            phase: "ready",
            items: prev.items.map((i) =>
              i.question_id === questionId
                ? { ...i, resolved: true, resolution: "self" as const }
                : i,
            ),
          }
        : prev,
    );
  }

  const items = state.items ?? [];
  const pageCount = Math.ceil(items.length / HISTORY_PAGE_SIZE);
  // Deleting the last item on the last page must not strand the view past
  // the new end — clamp instead of showing an empty page with pager controls
  // still pointing further than the (now shorter) list actually goes.
  const safePage = pageCount === 0 ? 0 : Math.min(page, pageCount - 1);
  const pageItems = items.slice(
    safePage * HISTORY_PAGE_SIZE,
    safePage * HISTORY_PAGE_SIZE + HISTORY_PAGE_SIZE,
  );

  return (
    <section className="mx-auto w-full max-w-3xl px-md py-lg">
      <PageBackLink href="/" label="ホームへ戻る" className="mb-sm" />
      <h1 className="mb-xs font-bold text-2xl text-on-surface">質問履歴</h1>
      <p className="mb-lg text-on-surface-variant text-sm">
        これまでにあなたが投稿した質問の一覧です。自分で解決できた質問は「自分で解決した」で記録でき、不要になった質問は削除できます。
      </p>

      {state.phase === "loading" ? (
        <p className="text-on-surface-variant text-sm">読み込み中…</p>
      ) : state.phase === "error" ? (
        <p className="text-on-surface-variant text-sm">
          履歴を取得できませんでした。時間をおいて再度お試しください。
        </p>
      ) : items.length > 0 ? (
        <>
          <ul className="flex flex-col gap-sm">
            {pageItems.map((item) => (
              <HistoryRow
                key={item.question_id}
                item={item}
                onDeleted={handleDeleted}
                onResolved={handleResolved}
              />
            ))}
          </ul>
          {pageCount > 1 ? (
            <div className="mt-md flex items-center justify-center gap-sm">
              <button
                type="button"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
                className="rounded-md border border-outline-variant px-sm py-xs text-on-surface-variant text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                前へ
              </button>
              <span className="text-on-surface-variant text-sm">
                {safePage + 1} / {pageCount}
              </span>
              <button
                type="button"
                disabled={safePage + 1 >= pageCount}
                onClick={() => setPage(safePage + 1)}
                className="rounded-md border border-outline-variant px-sm py-xs text-on-surface-variant text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                次へ
              </button>
            </div>
          ) : null}
        </>
      ) : (
        <p className="text-on-surface-variant text-sm">まだ質問はありません。</p>
      )}
    </section>
  );
}
