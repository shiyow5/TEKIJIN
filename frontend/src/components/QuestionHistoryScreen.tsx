"use client";

/**
 * Full question-history view (product-spec 画面1 補助 / #208/#F9).
 *
 * "最近のあなたの質問" caps at 5; this shows every one of the asker's own past
 * questions (a generous `limit`, not true pagination — the seeded/demo data
 * volumes stay well under it) and is where deletion lives (#207/#F8).
 */

import { QuestionCard } from "@/components/QuestionCard";
import { useCurrentUser } from "@/components/CurrentUserProvider";
import { ApiError, deleteQuestion, getRecentQuestions } from "@/lib/api-client";
import type { RecentQuestionItem } from "@/lib/api-types";
import Link from "next/link";
import { useEffect, useState } from "react";

type Phase = "loading" | "ready" | "error";

const HISTORY_LIMIT = 200;
const DELETE_ERROR = "削除に失敗しました。時間をおいて再度お試しください。";
const DELETE_BLOCKED = "対応中の依頼があるため、この質問は削除できません。";

export function QuestionHistoryScreen() {
  const { currentUserId } = useCurrentUser();
  const [phase, setPhase] = useState<Phase>("loading");
  const [items, setItems] = useState<RecentQuestionItem[]>([]);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (currentUserId === null) {
      setPhase("loading");
      return;
    }
    let active = true;
    setPhase("loading");
    getRecentQuestions(currentUserId, { limit: HISTORY_LIMIT })
      .then((next) => {
        if (active) {
          setItems(next);
          setPhase("ready");
        }
      })
      .catch(() => {
        if (active) setPhase("error");
      });
    return () => {
      active = false;
    };
  }, [currentUserId]);

  function handleDelete(item: RecentQuestionItem) {
    if (currentUserId === null || deletingId !== null) return;
    setDeletingId(item.question_id);
    setError(null);
    const previous = items;
    setItems((prev) => prev.filter((i) => i.question_id !== item.question_id)); // optimistic
    deleteQuestion(item.question_id, currentUserId)
      .catch((err: unknown) => {
        setItems(previous); // rollback
        setError(err instanceof ApiError && err.status === 409 ? DELETE_BLOCKED : DELETE_ERROR);
      })
      .finally(() => setDeletingId(null));
  }

  return (
    <section className="mx-auto flex w-full max-w-4xl flex-col gap-md py-lg">
      <header className="flex items-center justify-between gap-sm">
        <h1 className="font-bold text-2xl text-on-surface">質問の履歴</h1>
        <Link
          href="/questions"
          className="text-primary text-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          質問画面に戻る
        </Link>
      </header>

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-error-container bg-error-container p-sm text-on-error-container text-sm"
        >
          {error}
        </p>
      ) : null}

      {phase === "loading" ? (
        <p className="text-on-surface-variant text-sm">読み込み中…</p>
      ) : phase === "error" ? (
        <p className="text-on-surface-variant text-sm">
          履歴を取得できませんでした。時間をおいて再度お試しください。
        </p>
      ) : items.length > 0 ? (
        <ul className="grid grid-cols-1 gap-gutter md:grid-cols-2">
          {items.map((item) => (
            <li key={item.question_id}>
              {item.session_id ? (
                <Link
                  href={`/session/${encodeURIComponent(item.session_id)}`}
                  aria-label={`「${item.title}」（${item.resolved ? "解決済" : "対応中"}）の結果をもう一度見る`}
                  className="block h-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                >
                  <QuestionCard
                    item={item}
                    clickable={true}
                    onDelete={handleDelete}
                    deleting={deletingId === item.question_id}
                  />
                </Link>
              ) : (
                <QuestionCard
                  item={item}
                  clickable={false}
                  onDelete={handleDelete}
                  deleting={deletingId === item.question_id}
                />
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-on-surface-variant text-sm">まだ質問はありません。</p>
      )}
    </section>
  );
}
