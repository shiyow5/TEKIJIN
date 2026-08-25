"use client";

/**
 * Past-Q&A knowledge detail viewer (#293, #301) — the `kind="qa"` counterpart
 * to `DocumentViewer` (`kind="document"`, #143), which already had its own
 * stable page. Keyed by `sourceId` (`Answer.id`), the same id a self-answer's
 * QA citation carries (#291), so #321's chat citation chip can finally link
 * somewhere instead of showing a non-linked chip.
 */

import { getKnowledgeDetail } from "@/lib/api-client";
import type { KnowledgeItem } from "@/lib/api-types";
import Link from "next/link";
import { useEffect, useState } from "react";

type Phase = "loading" | "ready" | "notfound" | "error";

interface ViewerState {
  phase: Phase;
  item?: KnowledgeItem;
}

/** ISO 8601 → "YYYY-MM-DD" without locale/timezone drift (string slice). */
function formatDate(iso: string | null | undefined): string | null {
  if (!iso || iso.length < 10) return null;
  return iso.slice(0, 10);
}

export function KnowledgeDetailScreen({ sourceId }: { sourceId: string }) {
  const [state, setState] = useState<ViewerState>({ phase: "loading" });

  useEffect(() => {
    let active = true;
    setState({ phase: "loading" });
    getKnowledgeDetail(sourceId)
      .then((item) => {
        if (active) setState({ phase: "ready", item });
      })
      .catch((err: unknown) => {
        if (!active) return;
        const status = (err as { status?: number })?.status;
        setState({ phase: status === 404 ? "notfound" : "error" });
      });
    return () => {
      active = false;
    };
  }, [sourceId]);

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-lg py-lg">
      <Link href="/knowledge" className="text-primary text-sm hover:underline">
        ← ナレッジライブラリーへ戻る
      </Link>

      {state.phase === "loading" ? (
        <p className="text-on-surface-variant text-sm">読み込み中…</p>
      ) : state.phase === "notfound" ? (
        <div className="rounded-xl border border-outline-variant bg-surface-container-low p-lg">
          <h1 className="font-bold text-xl text-on-surface">ナレッジが見つかりません</h1>
          <p className="mt-sm text-on-surface-variant text-sm">
            指定された項目は存在しないか、削除された可能性があります。
          </p>
        </div>
      ) : state.phase === "error" ? (
        <div className="rounded-xl border border-error-container bg-error-container p-lg text-on-error-container">
          ナレッジを取得できませんでした。時間をおいて再度お試しください。
        </div>
      ) : state.item ? (
        <article className="flex flex-col gap-md rounded-xl border border-outline-variant bg-surface-container-lowest p-lg">
          <header className="flex flex-col gap-xs border-outline-variant border-b pb-md">
            <h1 className="font-bold text-2xl text-on-surface">{state.item.title}</h1>
            {formatDate(state.item.resolved_at) ? (
              <span className="text-on-surface-variant text-xs">
                更新日: {formatDate(state.item.resolved_at)}
              </span>
            ) : null}
            {state.item.topics.length > 0 ? (
              <div className="flex flex-wrap gap-xs pt-xs">
                {state.item.topics.map((topic) => (
                  <span
                    key={topic}
                    className="rounded-full bg-secondary-container px-xs py-[2px] text-on-secondary-container text-xs"
                  >
                    {topic}
                  </span>
                ))}
              </div>
            ) : null}
          </header>
          <p className="whitespace-pre-wrap text-on-surface leading-relaxed">
            {state.item.summary || "（回答本文はありません）"}
          </p>
          {state.item.session_id ? (
            <Link
              href={`/session/${encodeURIComponent(state.item.session_id)}`}
              className="text-primary text-sm hover:underline"
            >
              セッション結果を見る →
            </Link>
          ) : null}
        </article>
      ) : null}
    </section>
  );
}
