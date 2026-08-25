"use client";

/**
 * Admin dashboard (product-spec 画面5). Aggregate-only, by design: it never
 * shows individual question content ("監視ツールになった瞬間に、誰も本音の質問を
 * しなくなる"). Loads GET /dashboard once and renders the four headline metrics
 * (自己解決率 / 負荷分散 / 平均解決時間 / 推薦精度) plus load + topic distributions.
 */

import { ApiError, getDashboard } from "@/lib/api-client";
import type { DashboardResponse } from "@/lib/api-types";
import { PageBackLink } from "@/components/PageBackLink";
import { useEffect, useState } from "react";

type Phase = "loading" | "ready" | "error" | "forbidden";

interface DashboardState {
  phase: Phase;
  data?: DashboardResponse;
}

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function hours(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)} 時間`;
}

/** ms → a compact human string ("820ms" / "2.9s"), or "—" when unmeasured. */
function latency(value: number | null): string {
  if (value === null) return "—";
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`;
}

function MetricCard({
  label,
  value,
  hint,
  title,
}: {
  label: string;
  value: string;
  hint?: string;
  /** Optional hover tooltip explaining a metric in plain language. */
  title?: string;
}) {
  return (
    <div
      className="flex flex-col gap-xs rounded-xl border border-outline-variant bg-surface-container-lowest p-md shadow-sm"
      title={title}
    >
      <span className="text-on-surface-variant text-sm">{label}</span>
      <span className="font-bold text-3xl text-on-surface tracking-tight">{value}</span>
      {hint ? <span className="text-on-surface-variant text-xs">{hint}</span> : null}
    </div>
  );
}

function DistributionBars({
  items,
}: {
  items: { key: string; label: string; count: number }[];
}) {
  const max = Math.max(1, ...items.map((i) => i.count));
  return (
    <ul className="flex flex-col gap-xs">
      {items.map((item) => (
        <li key={item.key} className="flex items-center gap-sm text-sm">
          <span className="w-40 shrink-0 truncate text-on-surface" title={item.label}>
            {item.label}
          </span>
          <span className="h-3 flex-1 overflow-hidden rounded-full bg-surface-variant">
            <span
              className="block h-full rounded-full bg-primary"
              style={{ width: `${(item.count / max) * 100}%` }}
            />
          </span>
          <span className="w-10 shrink-0 text-right text-on-surface-variant tabular-nums">
            {item.count}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function Dashboard() {
  const [state, setState] = useState<DashboardState>({ phase: "loading" });

  useEffect(() => {
    let active = true;
    getDashboard()
      .then((data) => {
        if (active) setState({ phase: "ready", data });
      })
      .catch((err) => {
        if (!active) return;
        // 403 (non-admin session) is permanent — retrying never helps, unlike a
        // transient failure — so it needs its own message rather than being
        // folded into the generic "error" phase (#369).
        const forbidden = err instanceof ApiError && err.status === 403;
        setState({ phase: forbidden ? "forbidden" : "error" });
      });
    return () => {
      active = false;
    };
  }, []);

  if (state.phase === "loading") {
    return (
      <section className="mx-auto flex w-full max-w-5xl flex-col py-lg">
        <PageBackLink href="/" label="ホームへ戻る" className="mb-sm" />
        <h1 className="text-center font-bold text-2xl text-on-surface">
          ダッシュボードを読み込み中…
        </h1>
      </section>
    );
  }

  if (state.phase === "forbidden") {
    return (
      <section className="mx-auto flex w-full max-w-5xl flex-col gap-md py-lg text-center">
        <PageBackLink href="/" label="ホームへ戻る" />
        <h1 className="font-bold text-2xl text-on-surface">表示できませんでした</h1>
        <div
          role="alert"
          className="rounded-xl border border-outline-variant bg-surface-container p-md text-on-surface-variant"
        >
          このページを見る権限がありません。管理者アカウントでログインしてください。
        </div>
      </section>
    );
  }

  if (state.phase === "error" || !state.data) {
    return (
      <section className="mx-auto flex w-full max-w-5xl flex-col gap-md py-lg text-center">
        <PageBackLink href="/" label="ホームへ戻る" />
        <h1 className="font-bold text-2xl text-on-surface">表示できませんでした</h1>
        <div
          role="alert"
          className="rounded-xl border border-outline-variant bg-surface-container p-md text-on-surface-variant"
        >
          集計データの取得に失敗しました。時間をおいて再度お試しください。
        </div>
      </section>
    );
  }

  const d = state.data;
  const evalHint = d.latest_eval
    ? `候補上位3名の的中率 ${d.latest_eval.recall_at_3 !== null ? pct(d.latest_eval.recall_at_3) : "—"}`
    : "精度評価はまだ実行されていません";
  const evalTitle =
    "正解データで測った推薦精度です。最有力＝正解者を1番手に選べた割合、" +
    "的中率＝正解者が候補上位3名に入った割合。定期的な精度評価の実行時に更新されます。";
  const evalValue =
    d.latest_eval && d.latest_eval.top1_accuracy !== null
      ? pct(d.latest_eval.top1_accuracy)
      : "未計測";

  return (
    <section className="mx-auto flex w-full max-w-5xl flex-col gap-lg py-lg">
      <PageBackLink href="/" label="ホームへ戻る" className="-mb-sm" />
      <header className="flex flex-col gap-xs">
        <h1 className="font-bold text-2xl text-on-surface">ダッシュボード</h1>
        <p className="text-on-surface-variant text-sm">
          個人の質問内容は表示しません。集計のみを表示します。
        </p>
      </header>

      <div className="grid grid-cols-1 gap-md sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="自己解決率"
          value={pct(d.self_resolution_rate)}
          hint="補助経路で人を介さず解決"
        />
        <MetricCard
          label="上位1名への集中率"
          value={pct(d.top_responder_share)}
          hint="負荷分散（低いほど分散）"
        />
        <MetricCard
          label="平均解決時間"
          value={hours(d.avg_resolution_hours)}
          hint="質問→初回回答"
        />
        <MetricCard
          label="推薦精度（最有力）"
          value={evalValue}
          hint={evalHint}
          title={evalTitle}
        />
        <MetricCard
          label="応答速度（中央値）"
          value={latency(d.processing_latency.p50_ms)}
          hint={
            d.processing_latency.sample_size > 0
              ? `95%タイル ${latency(d.processing_latency.p95_ms)}・${d.processing_latency.sample_size}件`
              : "まだ計測データがありません"
          }
          title={
            "AIが質問を理解し取り次ぎ先を決めるまでの処理時間です（人の返信待ち時間は含みません）。" +
            "中央値=半数がこれより速い、95%タイル=95%がこれより速い。"
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-md sm:grid-cols-3">
        <MetricCard label="総質問数" value={String(d.total_questions)} />
        <MetricCard label="総回答数" value={String(d.total_answers)} />
        <MetricCard
          label="推薦の承認率"
          value={pct(d.acceptance_rate)}
          hint="accepted / 判定済み"
        />
      </div>

      <div className="grid grid-cols-1 gap-lg lg:grid-cols-2">
        <section className="flex flex-col gap-sm">
          <h2 className="font-bold text-on-surface text-sm">回答数の分布（負荷分散）</h2>
          {d.answers_per_responder.length > 0 ? (
            <DistributionBars
              items={d.answers_per_responder.map((r) => ({
                key: String(r.employee_id),
                label: r.name,
                count: r.answer_count,
              }))}
            />
          ) : (
            <p className="text-on-surface-variant text-sm">まだ回答がありません。</p>
          )}
        </section>

        <section className="flex flex-col gap-sm">
          <h2 className="font-bold text-on-surface text-sm">トピック分布</h2>
          {d.topic_distribution.length > 0 ? (
            <DistributionBars
              items={d.topic_distribution.map((t) => ({
                key: t.topic,
                label: t.topic,
                count: t.count,
              }))}
            />
          ) : (
            <p className="text-on-surface-variant text-sm">まだトピックがありません。</p>
          )}
        </section>
      </div>

      <section className="flex flex-col gap-sm">
        <h2 className="font-bold text-on-surface text-sm">
          フィードバック（AIの解釈・推薦・下書きのズレ / #237）
        </h2>
        {d.feedback_by_stage.total > 0 ? (
          <DistributionBars
            items={[
              { key: "c1", label: "解釈（C1）", count: d.feedback_by_stage.c1 },
              { key: "c6", label: "推薦（C6）", count: d.feedback_by_stage.c6 },
              { key: "c7", label: "下書き（C7）", count: d.feedback_by_stage.c7 },
            ]}
          />
        ) : (
          <p className="text-on-surface-variant text-sm">まだフィードバックはありません。</p>
        )}
      </section>
    </section>
  );
}
