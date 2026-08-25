"use client";

/**
 * ナレッジライブラリー（蓄積された形式知の一覧・検索画面, #293 / #301）。
 *
 * Unlike `HistoryScreen` (the acting user's OWN past questions), this lists
 * accumulated knowledge company-wide — both past Q&A (`kind="qa"`) and
 * internal documents (`kind="document"`), newest first — the point is "これに
 * 近い話、前にも誰かが聞いてたはず" / "そういう文書がある": discovering
 * something someone else already produced. Each `source_id`/`kind` matches
 * exactly what a self-answer's citation (#291) carries for the same entity,
 * so a chat citation and a knowledge-list card point at the same stable
 * thing — a `"qa"` card links to `/knowledge/{source_id}` (this app's own
 * detail viewer, new here), a `"document"` card links to the existing
 * `/documents/{source_id}` viewer (#143).
 *
 * A `"qa"` item needs an actual `answers` row (not merely an accepted
 * recommendation) — that is the only place answer TEXT lives, so without it
 * there is nothing to show as its `summary`.
 *
 * The search box forwards straight to GET /knowledge as the `q` query param
 * (server-side filtering). The department/topic/period filters GET /knowledge
 * also supports are deliberately not exposed here — keyword search only, kept
 * simple by request.
 *
 * The unsearched (browse) view is a single unpaginated page of the latest
 * `RESULT_LIMIT` items; once a search is active, results page through
 * `RESULT_LIMIT` at a time via `offset` (by request — pagination matters once
 * a keyword search can match more than one page's worth).
 *
 * The side panel's stats reuse the dashboard's existing self-resolution rate
 * (via `summary` on the same response) rather than introducing a new
 * aggregation. Per-responder counts are deliberately NOT shown here — that
 * view belongs to `/dashboard`, not a knowledge browser (PR #340 review).
 */

import { getKnowledgeList } from "@/lib/api-client";
import type { KnowledgeItem, KnowledgeSummary } from "@/lib/api-types";
import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

const RESULT_LIMIT = 8;

type Phase = "loading" | "ready" | "error";

interface KnowledgeState {
  phase: Phase;
  items?: KnowledgeItem[];
  totalMatching?: number;
  summary?: KnowledgeSummary;
}

/** "2026-08-20" from an ISO timestamp; "—" when unparseable/missing. */
function formatDate(iso: string | null | undefined): string {
  if (!iso || iso.length < 10) return "—";
  return iso.slice(0, 10);
}

/**
 * Deliberately minimal by request: no kind badge, no responder line — just
 * the title/summary/topics and a single unified "更新日" (the item's own
 * timestamp regardless of `kind`).
 */
function KnowledgeCard({ item }: { item: KnowledgeItem }) {
  const href =
    item.kind === "qa"
      ? `/knowledge/${encodeURIComponent(item.source_id)}`
      : `/documents/${encodeURIComponent(item.source_id)}`;

  return (
    <Link
      href={href}
      aria-label={`「${item.title}」を見る`}
      className="block h-full min-w-0 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
    >
      <article className="flex h-full min-w-0 flex-col gap-sm overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest p-md transition-shadow hover:shadow-md">
        <h3 className="break-words font-bold text-base text-on-surface">{item.title}</h3>
        {item.summary ? (
          <p className="line-clamp-3 break-words text-on-surface-variant text-sm">{item.summary}</p>
        ) : null}
        {item.topics.length > 0 ? (
          <div className="flex flex-wrap gap-xs">
            {item.topics.map((topic) => (
              <span
                key={topic}
                className="max-w-full break-words rounded-full bg-secondary-container px-xs py-[2px] text-on-secondary-container text-xs"
              >
                {topic}
              </span>
            ))}
          </div>
        ) : null}
        <div className="mt-auto flex flex-wrap items-center gap-x-md gap-y-xs border-outline-variant border-t pt-sm text-on-surface-variant text-xs">
          <span>更新日: {formatDate(item.resolved_at)}</span>
        </div>
      </article>
    </Link>
  );
}

function SummaryPanel({ summary }: { summary: KnowledgeSummary | undefined }) {
  return (
    <aside className="flex w-full flex-col gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md md:w-56">
      <h2 className="font-bold text-on-surface text-sm">蓄積状況</h2>
      <div>
        <p className="text-on-surface-variant text-xs">蓄積件数</p>
        <p className="font-bold text-2xl text-on-surface">
          {summary ? summary.total_items.toLocaleString() : "—"}
        </p>
      </div>
      <div>
        <p className="text-on-surface-variant text-xs">自己解決率</p>
        <p className="font-bold text-2xl text-on-surface">
          {summary ? `${Math.round(summary.self_resolution_rate * 100)}%` : "—"}
        </p>
      </div>
    </aside>
  );
}

export function KnowledgeScreen() {
  const [state, setState] = useState<KnowledgeState>({ phase: "loading" });
  const [q, setQ] = useState("");
  const [pendingQ, setPendingQ] = useState("");
  const [page, setPage] = useState(0);

  useEffect(() => {
    let active = true;
    setState((prev) => ({
      phase: "loading",
      items: prev.items,
      totalMatching: prev.totalMatching,
      summary: prev.summary,
    }));
    getKnowledgeList({ q: q || undefined, offset: page * RESULT_LIMIT, limit: RESULT_LIMIT })
      .then(({ items, total_matching, summary }) => {
        if (active) setState({ phase: "ready", items, totalMatching: total_matching, summary });
      })
      .catch(() => {
        if (active) setState({ phase: "error" });
      });
    return () => {
      active = false;
    };
  }, [q, page]);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(0);
    setQ(pendingQ);
  }

  function clearSearch() {
    setPendingQ("");
    setPage(0);
    setQ("");
  }

  const pageCount = state.totalMatching ? Math.ceil(state.totalMatching / RESULT_LIMIT) : 0;
  // Pagination only matters once a search is active — the plain browse view is
  // a single unpaginated page of the latest items (by request).
  const showPagination = q !== "" && pageCount > 1;

  return (
    <section className="mx-auto flex w-full max-w-5xl flex-col gap-lg px-md py-lg">
      <h1 className="text-center font-bold text-2xl text-on-surface">ナレッジライブラリー</h1>

      <form
        onSubmit={submitSearch}
        className="flex flex-col gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md"
      >
        <label className="flex flex-col gap-xs text-on-surface-variant text-xs">
          キーワード
          <input
            type="search"
            value={pendingQ}
            onChange={(e) => setPendingQ(e.target.value)}
            placeholder="質問のキーワード"
            className="rounded-md border border-outline bg-surface px-sm py-xs text-on-surface text-sm"
          />
        </label>
        <div className="flex items-center justify-center gap-sm">
          <button
            type="submit"
            className="rounded-md bg-primary px-md py-xs font-bold text-on-primary text-sm"
          >
            検索
          </button>
          {q ? (
            <button
              type="button"
              onClick={clearSearch}
              className="rounded-md px-md py-xs text-on-surface-variant text-sm hover:underline"
            >
              条件をクリア
            </button>
          ) : null}
        </div>
      </form>

      <div className="flex flex-col gap-lg md:flex-row md:items-start">
        <div className="flex-1">
          {state.phase === "loading" && !state.items ? (
            <p className="text-on-surface-variant text-sm">読み込み中…</p>
          ) : state.phase === "error" ? (
            <p className="text-on-surface-variant text-sm">
              ナレッジを取得できませんでした。時間をおいて再度お試しください。
            </p>
          ) : state.items && state.items.length > 0 ? (
            <ul className="grid grid-cols-1 gap-gutter">
              {state.items.map((item) => (
                <li key={item.source_id} className="min-w-0">
                  <KnowledgeCard item={item} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-on-surface-variant text-sm">
              条件に一致するナレッジが見つかりませんでした。
            </p>
          )}
          {showPagination ? (
            <div className="mt-md flex items-center justify-center gap-sm">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
                className="rounded-md border border-outline-variant px-sm py-xs text-on-surface-variant text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                前へ
              </button>
              <span className="text-on-surface-variant text-sm">
                {page + 1} / {pageCount}
              </span>
              <button
                type="button"
                disabled={page + 1 >= pageCount}
                onClick={() => setPage((p) => p + 1)}
                className="rounded-md border border-outline-variant px-sm py-xs text-on-surface-variant text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                次へ
              </button>
            </div>
          ) : null}
        </div>
        <SummaryPanel summary={state.summary} />
      </div>
    </section>
  );
}
