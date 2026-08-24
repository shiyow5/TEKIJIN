"use client";

/**
 * 質問履歴（全期間の一括管理ビュー, #208）。
 *
 * The question screen's "最近のあなたの質問" panel shows only the newest five (#125);
 * this screen lists ALL of the acting user's past questions, newest first, as the
 * place to review and delete old ones (#207 の削除機能の本格的な置き場所). Each row
 * links to its session result when replayable and carries a delete control.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import { QuestionDeleteButton } from "@/components/QuestionDeleteButton";
import { getRecentQuestions } from "@/lib/api-client";
import type { RecentQuestionItem } from "@/lib/api-types";
import Link from "next/link";
import { useEffect, useState } from "react";

/** The full history pulls far more than the 5-item panel; 200 is the API cap. */
const HISTORY_LIMIT = 200;

type Phase = "loading" | "ready" | "error";

interface HistoryState {
  phase: Phase;
  items?: RecentQuestionItem[];
}

/** "2026-08-20 10:00" from an ISO timestamp; null when unparseable. */
function formatDate(iso: string | null | undefined): string | null {
  if (!iso || iso.length < 16) return null;
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}

/** The resolution line: responder name, document self-resolve, or pending. */
function resolutionNote(item: RecentQuestionItem): string {
  if (item.responder_name) return `回答者: ${item.responder_name}`;
  if (item.resolution === "document") return "社内文書で回答";
  return "取り次ぎ先を調整中";
}

function HistoryRow({
  item,
  onDeleted,
}: {
  item: RecentQuestionItem;
  onDeleted: (questionId: string) => void;
}) {
  const date = formatDate(item.created_at);
  return (
    <li className="relative rounded-xl border border-outline-variant bg-surface-container-lowest p-md">
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
        {item.session_id ? (
          <Link
            href={`/session/${encodeURIComponent(item.session_id)}`}
            className="text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            結果を見る
          </Link>
        ) : (
          <span className="text-on-surface-variant">履歴のみ</span>
        )}
      </div>
      <QuestionDeleteButton
        questionId={item.question_id}
        title={item.title}
        onDeleted={onDeleted}
      />
    </li>
  );
}

export function HistoryScreen() {
  const { currentUserId } = useCurrentUser();
  const [state, setState] = useState<HistoryState>({ phase: "loading" });

  useEffect(() => {
    if (currentUserId === null) {
      setState({ phase: "loading" });
      return;
    }
    let active = true;
    setState({ phase: "loading" });
    getRecentQuestions(currentUserId, { limit: HISTORY_LIMIT })
      .then((items) => {
        if (active) setState({ phase: "ready", items });
      })
      .catch(() => {
        if (active) setState({ phase: "error" });
      });
    return () => {
      active = false;
    };
  }, [currentUserId]);

  /** Drop a just-deleted question from the list without a full re-fetch. */
  function handleDeleted(questionId: string) {
    setState((prev) =>
      prev.phase === "ready" && prev.items
        ? { phase: "ready", items: prev.items.filter((i) => i.question_id !== questionId) }
        : prev,
    );
  }

  return (
    <section className="mx-auto w-full max-w-3xl px-md py-lg">
      <h1 className="mb-xs font-bold text-2xl text-on-surface">質問履歴</h1>
      <p className="mb-lg text-on-surface-variant text-sm">
        これまでにあなたが投稿した質問の一覧です。不要になった質問は削除できます。
      </p>

      {state.phase === "loading" ? (
        <p className="text-on-surface-variant text-sm">読み込み中…</p>
      ) : state.phase === "error" ? (
        <p className="text-on-surface-variant text-sm">
          履歴を取得できませんでした。時間をおいて再度お試しください。
        </p>
      ) : state.items && state.items.length > 0 ? (
        <ul className="flex flex-col gap-sm">
          {state.items.map((item) => (
            <HistoryRow key={item.question_id} item={item} onDeleted={handleDeleted} />
          ))}
        </ul>
      ) : (
        <p className="text-on-surface-variant text-sm">まだ質問はありません。</p>
      )}
    </section>
  );
}
