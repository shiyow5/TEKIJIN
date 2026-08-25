"use client";

/**
 * ナレッジセンター（蓄積された形式知の一覧・検索画面, #293 / #301）。
 *
 * Unlike `HistoryScreen` (the acting user's OWN past questions), this lists
 * every question a PERSON has resolved company-wide — the point is "これに近い
 * 話、前にも誰かが聞いてたはず": discovering someone else's past answer. Each
 * card names the responder and their department (答えの出所は常に人) so it never
 * reads as an anonymous FAQ.
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
 * The side panels' stats reuse the dashboard's existing self-resolution rate
 * and top-answerers aggregates (via `summary` on the same response) rather
 * than introducing a new aggregation — per the issue's
 * "新規の集計ロジックは極力増やさない".
 */

import { getKnowledgeList } from "@/lib/api-client";
import type { KnowledgeItem, KnowledgeSummary } from "@/lib/api-types";
import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

const RESULT_LIMIT = 15;

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

function KnowledgeCard({ item }: { item: KnowledgeItem }) {
  const body = (
    <article className="flex h-full min-w-0 flex-col gap-sm overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest p-md transition-shadow hover:shadow-md">
      <h3 className="break-words font-bold text-base text-on-surface">{item.title}</h3>
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
        <span className="break-words">
          回答者: {item.responder_name ?? "不明"}
          {item.responder_department ? `（${item.responder_department}）` : ""}
        </span>
        <span>解決日: {formatDate(item.resolved_at)}</span>
      </div>
    </article>
  );

  return item.session_id ? (
    <Link
      href={`/session/${encodeURIComponent(item.session_id)}`}
      aria-label={`「${item.title}」の結果を見る`}
      className="block h-full min-w-0 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
    >
      {body}
    </Link>
  ) : (
    body
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

function TopRespondersPanel({ summary }: { summary: KnowledgeSummary | undefined }) {
  const responders = summary?.top_responders ?? [];
  if (responders.length === 0) return null;
  const max = Math.max(...responders.map((r) => r.answer_count));

  return (
    <aside className="flex w-full flex-col gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md md:w-56">
      <h2 className="font-bold text-on-surface text-sm">回答者別の件数</h2>
      <ul className="flex flex-col gap-xs">
        {responders.map((r) => (
          <li key={r.employee_id} className="flex flex-col gap-[2px]">
            <div className="flex items-baseline justify-between gap-sm text-xs">
              <span className="min-w-0 truncate text-on-surface">{r.name}</span>
              <span className="shrink-0 text-on-surface-variant">{r.answer_count}</span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-surface-container-high">
              <div
                className="h-1.5 rounded-full bg-primary"
                style={{ width: `${(r.answer_count / max) * 100}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
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
            <ul className="grid grid-cols-1 gap-gutter md:grid-cols-2">
              {state.items.map((item) => (
                <li key={item.question_id} className="min-w-0">
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
        <div className="flex w-full shrink-0 flex-col gap-lg md:w-56">
          <SummaryPanel summary={state.summary} />
          <TopRespondersPanel summary={state.summary} />
        </div>
      </div>
    </section>
  );
}
