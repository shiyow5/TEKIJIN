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
 * detail viewer), a `"document"` card links to the existing
 * `/documents/{source_id}` viewer (#143).
 *
 * A `"qa"` item needs an actual `answers` row (not merely an accepted
 * recommendation) — that is the only place answer TEXT lives, so without it
 * there is nothing to show as its `summary`.
 *
 * Filters (#293 DoD: keyword / department / topic / period) all forward
 * straight to GET /knowledge as query params (server-side). `department` and
 * `topic` are QA-specific (documents carry neither, so either filter excludes
 * them — the backend's own behavior, not something this screen re-decides).
 * The department/topic dropdown OPTIONS are derived client-side from an
 * unfiltered snapshot fetched once on mount, since there is no dedicated
 * "list departments" endpoint. Period is a single "この日以降" (`since`) date
 * — no end date — by request; the API's `until` param exists but is
 * deliberately not exposed here.
 *
 * Results always page through `RESULT_LIMIT` at a time via `offset`, including
 * the unfiltered browse view — a library whose whole point is "someone already
 * asked this" is unusable if only the newest page is ever reachable, and a
 * reader who cannot think of a keyword has no other way in.
 *
 * The side panel's stats reuse the dashboard's existing self-resolution rate
 * (via `summary` on the same response) rather than introducing a new
 * aggregation. Per-responder counts are deliberately NOT shown here — that
 * view belongs to `/dashboard`, not a knowledge browser (PR #340 review).
 */

import { PageBackLink } from "@/components/PageBackLink";
import { getKnowledgeList } from "@/lib/api-client";
import type { KnowledgeItem, KnowledgeSummary } from "@/lib/api-types";
import { formatDateJst } from "@/lib/datetime";
import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

const RESULT_LIMIT = 5;

type Phase = "loading" | "ready" | "error";

interface KnowledgeState {
  phase: Phase;
  items?: KnowledgeItem[];
  totalMatching?: number;
  summary?: KnowledgeSummary;
}

interface Filters {
  q: string;
  department: string;
  topic: string;
  since: string;
}

const EMPTY_FILTERS: Filters = { q: "", department: "", topic: "", since: "" };

/** "2026-08-20" (JST) from an ISO timestamp; "—" when unparseable/missing. */
function formatDate(iso: string | null | undefined): string {
  return formatDateJst(iso) ?? "—";
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
  const [options, setOptions] = useState<{ departments: string[]; topics: string[] }>({
    departments: [],
    topics: [],
  });
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [pending, setPending] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);

  // Unfiltered snapshot, once, purely to populate the department/topic dropdown
  // options (there is no dedicated "list departments" endpoint).
  useEffect(() => {
    let active = true;
    getKnowledgeList({ limit: 200 })
      .then(({ items }) => {
        if (!active) return;
        const departments = [
          ...new Set(items.map((i) => i.responder_department).filter((d): d is string => !!d)),
        ].sort();
        const topics = [...new Set(items.flatMap((i) => i.topics))].sort();
        setOptions({ departments, topics });
      })
      .catch(() => {
        // Options are a convenience; a failed snapshot just leaves the dropdowns empty.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    setState((prev) => ({
      phase: "loading",
      items: prev.items,
      totalMatching: prev.totalMatching,
      summary: prev.summary,
    }));
    getKnowledgeList({
      q: filters.q || undefined,
      department: filters.department || undefined,
      topic: filters.topic || undefined,
      since: filters.since || undefined,
      offset: page * RESULT_LIMIT,
      limit: RESULT_LIMIT,
    })
      .then(({ items, total_matching, summary }) => {
        if (active) setState({ phase: "ready", items, totalMatching: total_matching, summary });
      })
      .catch(() => {
        if (active) setState({ phase: "error" });
      });
    return () => {
      active = false;
    };
  }, [filters, page]);

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(0);
    setFilters(pending);
  }

  function clearFilters() {
    setPending(EMPTY_FILTERS);
    setPage(0);
    setFilters(EMPTY_FILTERS);
  }

  const hasActiveFilters = Object.values(filters).some((v) => v !== "");
  const pageCount = state.totalMatching ? Math.ceil(state.totalMatching / RESULT_LIMIT) : 0;
  // Shown whenever there is more than one page — browsing included. Gating this
  // on an active filter left everything past the newest page unreachable unless
  // the reader already knew a keyword to search for.
  const showPagination = pageCount > 1;

  return (
    <section className="mx-auto flex w-full max-w-5xl flex-col gap-lg px-md py-lg">
      <PageBackLink href="/" label="ホームへ戻る" className="self-center md:self-start" />
      <h1 className="text-center font-bold text-2xl text-on-surface">ナレッジライブラリー</h1>

      <form onSubmit={submitFilters} className="flex w-full flex-col gap-md">
        {/* The keyword box is the primary action, so it is sized like one: the
            same bordered, shadowed, focus-highlighted bar the question input
            uses, with the submit inline. It used to be a `text-sm` field
            stacked with the three filters inside one card, which read as "four
            equal inputs" rather than "search, plus optional narrowing". */}
        <div className="flex w-full items-center gap-xs rounded-xl border border-outline-variant bg-surface-container-lowest px-sm py-xs shadow-sm focus-within:border-primary">
          <input
            type="search"
            value={pending.q}
            onChange={(e) => setPending((p) => ({ ...p, q: e.target.value }))}
            aria-label="キーワードで検索"
            placeholder="キーワードで検索（例: VPN, 経費精算）"
            className="w-full bg-transparent px-sm py-2 text-base text-on-surface outline-none placeholder:text-on-surface-variant"
          />
          <button
            type="submit"
            className="shrink-0 rounded-lg bg-primary px-md py-sm font-bold text-on-primary text-sm transition-colors hover:bg-primary-container"
          >
            検索
          </button>
        </div>

        {/* The three narrowing controls are grouped under their own heading so
            they read as optional refinements of the search above, not as more
            things that must be filled in. */}
        <fieldset className="flex flex-col gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md">
          <legend className="px-xs font-bold text-on-surface-variant text-xs">絞り込み</legend>
          <div className="grid w-full grid-cols-1 gap-sm sm:grid-cols-3">
            <label className="flex flex-col gap-xs text-on-surface-variant text-xs">
              部署
              <select
                value={pending.department}
                onChange={(e) => setPending((p) => ({ ...p, department: e.target.value }))}
                className="w-full rounded-md border border-outline bg-surface px-sm py-xs text-on-surface text-sm"
              >
                <option value="">すべて</option>
                {options.departments.map((dept) => (
                  <option key={dept} value={dept}>
                    {dept}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-xs text-on-surface-variant text-xs">
              トピック
              <select
                value={pending.topic}
                onChange={(e) => setPending((p) => ({ ...p, topic: e.target.value }))}
                className="w-full rounded-md border border-outline bg-surface px-sm py-xs text-on-surface text-sm"
              >
                <option value="">すべて</option>
                {options.topics.map((topic) => (
                  <option key={topic} value={topic}>
                    {topic}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-xs text-on-surface-variant text-xs">
              期間（この日以降）
              <input
                type="date"
                value={pending.since}
                onChange={(e) => setPending((p) => ({ ...p, since: e.target.value }))}
                className="w-full rounded-md border border-outline bg-surface px-sm py-xs text-on-surface text-sm"
              />
            </label>
          </div>
          <div className="flex items-center justify-end gap-sm">
            <button
              type="submit"
              className="rounded-md border border-outline px-md py-xs text-on-surface text-sm transition-colors hover:bg-surface-container-low"
            >
              絞り込む
            </button>
            {hasActiveFilters ? (
              <button
                type="button"
                onClick={clearFilters}
                className="rounded-md px-md py-xs text-on-surface-variant text-sm hover:underline"
              >
                条件をクリア
              </button>
            ) : null}
          </div>
        </fieldset>
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
