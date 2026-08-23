"use client";

/**
 * Responder inbox (#123): the questions currently handed off to the acting user.
 *
 * Loads GET /inbox for the current user (the header switcher's selection) and
 * lists each pending handoff, deep-linking to `/answer/{session_id}` — the entry
 * point the responder side previously had no in-app route to. Re-fetches when the
 * acting user changes. The full handoff (draft, reasons, reuse) loads on the
 * answer screen; here we show just enough to triage.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import { getInbox } from "@/lib/api-client";
import type { InboxItem } from "@/lib/api-types";
import Link from "next/link";
import { useEffect, useState } from "react";

type Phase = "loading" | "ready" | "error";

interface InboxState {
  phase: Phase;
  items?: InboxItem[];
}

/** ISO 8601 → "YYYY-MM-DD HH:mm" without locale/timezone drift (string slice). */
function formatReceivedAt(iso: string | null | undefined): string | null {
  if (!iso || iso.length < 16) return null;
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}

export function InboxScreen() {
  const { currentUserId, currentUser } = useCurrentUser();
  const [state, setState] = useState<InboxState>({ phase: "loading" });

  useEffect(() => {
    if (currentUserId === null) {
      setState({ phase: "loading" });
      return;
    }
    let active = true;
    setState({ phase: "loading" });
    getInbox(currentUserId)
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

  const who = currentUser?.name ? `${currentUser.name} さん宛て` : "あなた宛て";

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-lg py-lg">
      <header className="flex flex-col gap-xs">
        <h1 className="font-bold text-2xl text-on-surface">受信箱</h1>
        <p className="text-on-surface-variant text-sm">{who}に届いた質問です。</p>
      </header>

      {state.phase === "loading" ? (
        <p className="text-on-surface-variant text-sm">読み込み中…</p>
      ) : state.phase === "error" ? (
        <div
          role="alert"
          className="rounded-xl border border-outline-variant bg-surface-container p-md text-on-surface-variant"
        >
          受信箱の取得に失敗しました。時間をおいて再度お試しください。
        </div>
      ) : state.items && state.items.length > 0 ? (
        <ul className="flex flex-col gap-md">
          {state.items.map((item) => {
            const received = formatReceivedAt(item.created_at);
            return (
              <li key={item.session_id}>
                <Link
                  href={`/answer/${item.session_id}`}
                  className="flex flex-col gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md shadow-sm transition-colors hover:bg-surface-container-low"
                >
                  <div className="flex items-baseline justify-between gap-sm">
                    <span className="font-bold text-on-surface">
                      {item.asker.name ?? "匿名"} さんからの質問
                    </span>
                    {received ? (
                      <span className="shrink-0 text-on-surface-variant text-xs tabular-nums">
                        {received}
                      </span>
                    ) : null}
                  </div>
                  <p className="line-clamp-2 text-on-surface-variant text-sm">{item.question}</p>
                  {item.topics.length > 0 ? (
                    <ul className="flex flex-wrap gap-xs">
                      {item.topics.map((topic) => (
                        <li
                          key={topic}
                          className="rounded-full bg-secondary-container px-sm py-xs text-on-secondary-container text-xs"
                        >
                          {topic}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </Link>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="rounded-xl border border-outline-variant border-dashed bg-surface-container-lowest p-lg text-center text-on-surface-variant">
          <p>いまは届いている質問はありません。</p>
        </div>
      )}
    </section>
  );
}
