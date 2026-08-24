"use client";

/**
 * Responder inbox (#123): the questions currently handed off to the acting user.
 *
 * A list+detail layout (mirroring ChatScreen's pane split): the pending handoffs
 * on the left, and the selected one's full detail — question, selection reason,
 * draft, and the 引き受ける/今は難しい/自分より適任がいる actions — on the right,
 * reusing AnswerScreen so opening the inbox shows the first pending item's detail
 * immediately, with no extra navigation. `/answer/{session_id}` still exists as a
 * standalone deep link (e.g. from a notification), unaffected by this page.
 */

import { AnswerScreen } from "@/components/AnswerScreen";
import { useCurrentUser } from "@/components/CurrentUserProvider";
import type { HandoffAction } from "@/hooks/useHandoff";
import { getInbox } from "@/lib/api-client";
import type { InboxItem } from "@/lib/api-types";
import { useCallback, useEffect, useState } from "react";

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

function InboxListItem({
  item,
  active,
  onSelect,
}: {
  item: InboxItem;
  active: boolean;
  onSelect: () => void;
}) {
  const received = formatReceivedAt(item.created_at);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        className={
          active
            ? "flex w-full flex-col gap-sm rounded-xl border border-primary bg-secondary-container p-md text-left text-on-secondary-container shadow-sm"
            : "flex w-full flex-col gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md text-left shadow-sm transition-colors hover:bg-surface-container-low"
        }
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
      </button>
    </li>
  );
}

export function InboxScreen() {
  const { currentUserId, currentUser } = useCurrentUser();
  const [state, setState] = useState<InboxState>({ phase: "loading" });
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  // Below `md` the list and the detail take turns instead of sitting side by
  // side — two panes do not fit at phone width (#254 の流儀に合わせる).
  const [showDetail, setShowDetail] = useState(false);

  // `advanceToFirst` selects the fresh list's first item once it arrives —
  // done inside the same state update as the list itself (never as a separate
  // "clear now, pick later" step), so a declined item's stale pre-refresh
  // presence can never get re-selected in between.
  const load = useCallback(
    (advanceToFirst: boolean) => {
      if (currentUserId === null) {
        setState({ phase: "loading" });
        return;
      }
      let active = true;
      // Keep any already-loaded items visible while a background refresh
      // (e.g. after accepting) is in flight, instead of flashing to empty.
      setState((prev) => ({ phase: "loading", items: prev.items }));
      getInbox(currentUserId)
        .then((items) => {
          if (!active) return;
          setState({ phase: "ready", items });
          if (advanceToFirst) {
            setSelectedSessionId(items.length > 0 ? items[0].session_id : null);
          }
        })
        .catch(() => {
          if (active) setState({ phase: "error" });
        });
      return () => {
        active = false;
      };
    },
    [currentUserId],
  );

  useEffect(() => {
    setSelectedSessionId(null);
    setState({ phase: "loading" });
    return load(true);
  }, [load]);

  function handleDone(action: HandoffAction) {
    // "今は難しい" / "自分より適任がいる": drop it and move straight to
    // whatever's next once the refreshed list confirms it. "引き受ける"
    // instead keeps AnswerScreen's own "done" confirmation (with the
    // チャットを開く CTA) up — just refresh the list in the background, and
    // leave the current selection alone.
    load(action !== "answer");
    // Declining removes the item, so on mobile — where the detail replaces the
    // list — go back rather than sitting on a pane about to be re-pointed.
    if (action !== "answer") setShowDetail(false);
  }

  const who = currentUser?.name ? `${currentUser.name} さん宛て` : "あなた宛て";

  return (
    <section className="mx-auto flex w-full max-w-5xl gap-lg py-lg">
      <div
        className={`w-full flex-col gap-md md:max-w-sm md:shrink-0 ${
          showDetail ? "hidden md:flex" : "flex"
        }`}
      >
        <header className="flex flex-col gap-xs">
          <h1 className="font-bold text-2xl text-on-surface">受信箱</h1>
          <p className="text-on-surface-variant text-sm">{who}に届いた質問です。</p>
        </header>

        {state.phase === "loading" && !state.items ? (
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
            {state.items.map((item) => (
              <InboxListItem
                key={item.session_id}
                item={item}
                active={item.session_id === selectedSessionId}
                onSelect={() => {
                  setSelectedSessionId(item.session_id);
                  setShowDetail(true);
                }}
              />
            ))}
          </ul>
        ) : (
          <div className="rounded-xl border border-outline-variant border-dashed bg-surface-container-lowest p-lg text-center text-on-surface-variant">
            <p>いまは届いている質問はありません。</p>
          </div>
        )}
      </div>

      {selectedSessionId ? (
        <div
          className={`min-w-0 flex-1 flex-col gap-sm md:border-outline-variant md:border-l md:pl-lg ${
            showDetail ? "flex" : "hidden md:flex"
          }`}
        >
          <button
            type="button"
            onClick={() => setShowDetail(false)}
            className="-ml-xs self-start rounded-md px-xs py-1 text-on-surface-variant text-sm transition-colors hover:bg-surface-container-low md:hidden"
          >
            ← 受信箱へ戻る
          </button>
          <AnswerScreen sessionId={selectedSessionId} onDone={handleDone} />
        </div>
      ) : (
        // The chat screen shows the same hint; keep the two split views
        // consistent instead of leaving one half blank.
        <div className="hidden flex-1 items-center justify-center border-outline-variant border-l pl-lg text-on-surface-variant text-sm md:flex">
          一覧から質問を選んでください。
        </div>
      )}
    </section>
  );
}
