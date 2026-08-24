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
import { QuestionDeleteButton } from "@/components/QuestionDeleteButton";
import { getRecentQuestions } from "@/lib/api-client";
import type { RecentQuestionItem } from "@/lib/api-types";
import Link from "next/link";
import { useEffect, useState } from "react";

type Phase = "loading" | "ready" | "error";

interface RecentState {
  phase: Phase;
  items?: RecentQuestionItem[];
}

/** First character of the responder's name, for the avatar chip. */
function avatarInitial(name: string): string {
  return name.slice(0, 1);
}

/** The card body for one recent question (status chip + responder/document/pending footer).
 *
 * ``clickable`` distinguishes a card that navigates to its session result from a
 * seeded history row with no session: only the former gets the hover affordance,
 * and the latter is marked 「履歴のみ」 so it does not look pressable-but-dead (#179).
 */
function QuestionCard({ item, clickable }: { item: RecentQuestionItem; clickable: boolean }) {
  return (
    <article
      className={
        clickable
          ? "flex h-full flex-col rounded-xl border border-outline-variant bg-surface-container-lowest p-md transition-shadow hover:shadow-md"
          : "flex h-full flex-col rounded-xl border border-outline-variant border-dashed bg-surface-container-low p-md"
      }
    >
      <div className="mb-sm flex items-start justify-between gap-sm">
        <h3 className="font-bold text-lg text-on-surface">{item.title}</h3>
        <div className="flex items-center gap-xs">
          {clickable ? null : (
            <span className="whitespace-nowrap rounded-full bg-surface-container-high px-xs py-[2px] text-on-surface-variant text-xs">
              履歴のみ
            </span>
          )}
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
      </div>
      {item.responder_name ? (
        <div className="mt-auto flex items-center gap-sm border-outline-variant border-t pt-sm">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary-container font-bold text-on-secondary-container text-sm">
            {avatarInitial(item.responder_name)}
          </div>
          <div className="flex flex-col">
            <span className="text-on-surface-variant text-xs">回答者</span>
            <span className="text-on-surface text-sm">{item.responder_name}</span>
          </div>
        </div>
      ) : item.resolution === "self" ? (
        <div className="mt-auto flex items-center gap-sm border-outline-variant border-t pt-sm text-on-surface-variant text-sm">
          <span aria-hidden="true">✅</span>
          <span>自分で解決</span>
        </div>
      ) : item.resolution === "document" ? (
        <div className="mt-auto flex items-center gap-sm border-outline-variant border-t pt-sm text-on-surface-variant text-sm">
          <span aria-hidden="true">📄</span>
          <span>社内文書で回答</span>
        </div>
      ) : (
        <div className="mt-auto border-outline-variant border-t pt-sm text-on-surface-variant text-xs">
          取り次ぎ先を調整中です。
        </div>
      )}
    </article>
  );
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

  /** Drop a just-deleted question from the list without a full re-fetch (#207). */
  function handleDeleted(questionId: string) {
    setState((prev) =>
      prev.phase === "ready" && prev.items
        ? { phase: "ready", items: prev.items.filter((i) => i.question_id !== questionId) }
        : prev,
    );
  }

  return (
    <section className="mt-lg w-full">
      <h2 className="mb-md px-xs font-bold text-on-surface text-xl">最近のあなたの質問</h2>

      {state.phase === "loading" ? (
        <p className="px-xs text-on-surface-variant text-sm">読み込み中…</p>
      ) : state.phase === "error" ? (
        <p className="px-xs text-on-surface-variant text-sm">
          履歴を取得できませんでした。時間をおいて再度お試しください。
        </p>
      ) : state.items && state.items.length > 0 ? (
        <ul className="grid grid-cols-1 gap-gutter md:grid-cols-2">
          {state.items.map((item) => (
            <li key={item.question_id} className="relative">
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
              <QuestionDeleteButton
                questionId={item.question_id}
                title={item.title}
                onDeleted={handleDeleted}
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-xs text-on-surface-variant text-sm">まだ質問はありません。</p>
      )}
    </section>
  );
}
