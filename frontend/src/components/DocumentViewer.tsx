"use client";

/**
 * Internal-document viewer (#143).
 *
 * When a question is answered by the `document` route, the terminal `message`
 * event carries the cited `doc_id`. This screen fetches that document
 * (GET /documents/{id}) and shows its full content, so the asker can actually
 * read the relevant material instead of just being told an id.
 */

import { PageBackLink } from "@/components/PageBackLink";
import { getDocument } from "@/lib/api-client";
import type { DocumentDetail } from "@/lib/api-types";
import { useEffect, useState } from "react";

type Phase = "loading" | "ready" | "notfound" | "error";

interface ViewerState {
  phase: Phase;
  doc?: DocumentDetail;
}

/** ISO 8601 → "YYYY-MM-DD" without locale/timezone drift (string slice). */
function formatUpdatedAt(iso: string | null | undefined): string | null {
  if (!iso || iso.length < 10) return null;
  return iso.slice(0, 10);
}

export function DocumentViewer({
  docId,
  fromSessionId,
}: {
  docId: string;
  fromSessionId?: string;
}) {
  const [state, setState] = useState<ViewerState>({ phase: "loading" });
  // Document links originate on the session processing screen. Returning to
  // `/result` loses that screen and shows only the replayed terminal message,
  // so take the reader back to the actual origin route (#342).
  //
  // The label says 回答, not 結果: `/session/{id}/result` is a *different*
  // screen, and the origin screen offers its own 「結果を見る」 button pointing
  // there. Calling this one 「結果へ戻る」 would put two 結果 labels on adjacent
  // screens leading to two different places. The origin is headed
  // 「回答をお届けします」 whenever this link is shown — a document link only
  // exists on a terminal `message` — so 回答 is what the reader left (#126).
  const backHref = fromSessionId ? `/session/${fromSessionId}` : "/";
  const backLabel = fromSessionId ? "回答へ戻る" : "ホームへ戻る";

  useEffect(() => {
    let active = true;
    setState({ phase: "loading" });
    getDocument(docId)
      .then((doc) => {
        if (active) setState({ phase: "ready", doc });
      })
      .catch((err: unknown) => {
        if (!active) return;
        const status = (err as { status?: number })?.status;
        setState({ phase: status === 404 ? "notfound" : "error" });
      });
    return () => {
      active = false;
    };
  }, [docId]);

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-lg py-lg">
      <PageBackLink href={backHref} label={backLabel} className="-mb-sm" />

      {state.phase === "loading" ? (
        <p className="text-on-surface-variant text-sm">読み込み中…</p>
      ) : state.phase === "notfound" ? (
        <div className="rounded-xl border border-outline-variant bg-surface-container-low p-lg">
          <h1 className="font-bold text-xl text-on-surface">文書が見つかりません</h1>
          <p className="mt-sm text-on-surface-variant text-sm">
            指定された文書は存在しないか、削除された可能性があります。
          </p>
        </div>
      ) : state.phase === "error" ? (
        <div className="rounded-xl border border-error-container bg-error-container p-lg text-on-error-container">
          文書を取得できませんでした。時間をおいて再度お試しください。
        </div>
      ) : state.doc ? (
        <article className="flex flex-col gap-md rounded-xl border border-outline-variant bg-surface-container-lowest p-lg">
          <header className="flex flex-col gap-xs border-outline-variant border-b pb-md">
            <span className="text-on-surface-variant text-xs">社内文書</span>
            <h1 className="font-bold text-2xl text-on-surface">
              {state.doc.title || "（無題の文書）"}
            </h1>
            <div className="flex flex-wrap gap-md text-on-surface-variant text-xs">
              {state.doc.source ? <span>出典: {state.doc.source}</span> : null}
              {formatUpdatedAt(state.doc.updated_at) ? (
                <span>更新: {formatUpdatedAt(state.doc.updated_at)}</span>
              ) : null}
              <span>ID: {state.doc.id}</span>
            </div>
          </header>
          <p className="whitespace-pre-wrap text-on-surface leading-relaxed">
            {state.doc.body || "（本文はありません）"}
          </p>
        </article>
      ) : null}
    </section>
  );
}
