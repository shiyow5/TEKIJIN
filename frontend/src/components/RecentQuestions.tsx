"use client";

/**
 * "最近のあなたの質問" list on the question screen.
 *
 * Fetches the acting user's own recent questions (GET /questions) and shows each
 * with its resolution state and responder — replacing the previous hardcoded
 * mock (#125). Re-fetches when the acting user changes; renders nothing but a
 * quiet placeholder while there is no user or no history.
 *
 * A question with a ``session_id`` links to ``/session/{session_id}`` so the
 * asker can re-view its result (the run replays over /events) — #150. Seeded
 * history with no session is shown non-clickable.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import { QuestionCard } from "@/components/QuestionCard";
import { getRecentQuestions } from "@/lib/api-client";
import type { RecentQuestionItem } from "@/lib/api-types";
import Link from "next/link";
import { useEffect, useState } from "react";

type Phase = "loading" | "ready" | "error";

interface RecentState {
  phase: Phase;
  items?: RecentQuestionItem[];
}

export function RecentQuestions() {
  const { currentUserId } = useCurrentUser();
  const [state, setState] = useState<RecentState>({ phase: "loading" });

  useEffect(() => {
    if (currentUserId === null) {
      setState({ phase: "loading" });
      return;
    }
    let active = true;
    setState({ phase: "loading" });
    getRecentQuestions(currentUserId)
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

  return (
    <section className="mt-lg w-full">
      <div className="mb-md flex items-center justify-between px-xs">
        <h2 className="font-bold text-on-surface text-xl">最近のあなたの質問</h2>
        <Link
          href="/questions/history"
          className="text-primary text-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          すべて見る
        </Link>
      </div>

      {state.phase === "loading" ? (
        <p className="px-xs text-on-surface-variant text-sm">読み込み中…</p>
      ) : state.phase === "error" ? (
        <p className="px-xs text-on-surface-variant text-sm">
          履歴を取得できませんでした。時間をおいて再度お試しください。
        </p>
      ) : state.items && state.items.length > 0 ? (
        <ul className="grid grid-cols-1 gap-gutter md:grid-cols-2">
          {state.items.map((item) => (
            <li key={item.question_id}>
              {item.session_id ? (
                <Link
                  href={`/session/${encodeURIComponent(item.session_id)}`}
                  aria-label={`「${item.title}」（${item.resolved ? "解決済" : "対応中"}）の結果をもう一度見る`}
                  className="block h-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                >
                  <QuestionCard item={item} clickable={true} />
                </Link>
              ) : (
                <QuestionCard item={item} clickable={false} />
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-xs text-on-surface-variant text-sm">まだ質問はありません。</p>
      )}
    </section>
  );
}
