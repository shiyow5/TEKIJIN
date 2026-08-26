"use client";

/**
 * Chat page (#224): the accepted-recommendation threads for the acting user.
 *
 * A Slack-style two-pane layout — a thread list on the left (every accepted
 * hand-off where the acting user is asker or responder, newest activity
 * first), and the selected thread's message history + send form on the
 * right. Both panes poll (see {@link useChatThreads} / {@link useChatThread})
 * rather than subscribing to a stream, per the issue's scope.
 *
 * This is also the responder's recovery path for #224's other gap: reloading
 * `/answer/{session_id}` after accepting 404s (the `send` interrupt is already
 * consumed), so `AnswerScreen` links here — the thread list finds the
 * conversation even when the session id is gone.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import { PageBackLink } from "@/components/PageBackLink";
import { SlackLinkButton } from "@/components/SlackLinkButton";
import { useChatThread } from "@/hooks/useChatThread";
import { useChatThreads } from "@/hooks/useChatThreads";
import type { ChatMessage, ChatThreadSummary } from "@/lib/api-types";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export interface ChatScreenProps {
  /** Deep-link a specific thread open (`?thread=<recommendation_id>`), e.g. from AnswerScreen. */
  initialThreadId?: string;
  /** Result of a just-completed Slack OAuth round trip (`?slack=linked|error`). */
  initialSlackResult?: "linked" | "error";
}

/** ISO 8601 → "YYYY-MM-DD HH:mm" without locale/timezone drift (string slice). */
function formatTimestamp(iso: string | null | undefined): string | null {
  if (!iso || iso.length < 16) return null;
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}

function ChatThreadListItem({
  thread,
  active,
  onSelect,
}: {
  thread: ChatThreadSummary;
  active: boolean;
  onSelect: () => void;
}) {
  const when = formatTimestamp(thread.last_message_at ?? thread.created_at);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        className={
          active
            ? "flex w-full items-start justify-between gap-sm rounded-lg bg-secondary-container px-sm py-sm text-left text-on-secondary-container"
            : "flex w-full items-start justify-between gap-sm rounded-lg px-sm py-sm text-left transition-colors hover:bg-surface-container-low"
        }
      >
        <span className="flex min-w-0 flex-col gap-[2px]">
          <span className="truncate font-bold text-on-surface text-sm">
            {thread.counterpart.name ?? "匿名"}
          </span>
          {/* Two accepted requests with the same person are otherwise
              indistinguishable in this list (#224 review). */}
          {thread.question_title ? (
            <span className="truncate text-on-surface-variant text-xs">
              {thread.question_title}
            </span>
          ) : null}
        </span>
        {when ? (
          <span className="shrink-0 text-on-surface-variant text-xs tabular-nums">{when}</span>
        ) : null}
      </button>
    </li>
  );
}

function ChatThreadList({
  threads,
  phase,
  selectedId,
  onSelect,
  className,
}: {
  threads: ChatThreadSummary[];
  phase: "loading" | "ready" | "error";
  selectedId: number | null;
  onSelect: (id: number) => void;
  className: string;
}) {
  return (
    <div
      className={`w-full flex-col gap-sm md:max-w-xs md:shrink-0 md:border-outline-variant md:border-r md:pr-md ${className}`}
    >
      <PageBackLink href="/" label="ホームへ戻る" />
      <h1 className="font-bold text-lg text-on-surface">チャット</h1>
      {phase === "loading" && threads.length === 0 ? (
        <p className="text-on-surface-variant text-sm">読み込み中…</p>
      ) : phase === "error" && threads.length === 0 ? (
        <p role="alert" className="text-on-surface-variant text-sm">
          一覧の取得に失敗しました。時間をおいて再度お試しください。
        </p>
      ) : threads.length === 0 ? (
        <div className="rounded-xl border border-outline-variant border-dashed p-md text-center text-on-surface-variant text-sm">
          承諾済みの依頼がまだありません。
        </div>
      ) : (
        <ul aria-label="チャットスレッド一覧" className="flex flex-col gap-xs overflow-y-auto">
          {threads.map((thread) => (
            <ChatThreadListItem
              key={thread.thread_id}
              thread={thread}
              active={thread.thread_id === selectedId}
              onSelect={() => onSelect(thread.thread_id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function ChatBubble({ message, mine }: { message: ChatMessage; mine: boolean }) {
  const when = formatTimestamp(message.created_at);
  return (
    <li className={`flex flex-col ${mine ? "items-end" : "items-start"}`}>
      <div
        className={
          mine
            ? "max-w-md rounded-xl rounded-tr-sm bg-primary px-sm py-xs text-on-primary"
            : "max-w-md rounded-xl rounded-tl-sm bg-surface-container px-sm py-xs text-on-surface"
        }
      >
        <p className="whitespace-pre-wrap text-sm">{message.body}</p>
      </div>
      {when ? <span className="mt-[2px] text-on-surface-variant text-xs">{when}</span> : null}
    </li>
  );
}

function ChatConversation({
  threadId,
  currentUserId,
  className,
  onBack,
}: {
  threadId: number | null;
  currentUserId: string | null;
  className: string;
  onBack: () => void;
}) {
  const { phase, detail, send, sending, sendError } = useChatThread(threadId, currentUserId);
  const [draft, setDraft] = useState("");

  const canSend = draft.trim().length > 0 && !sending;

  function handleSend() {
    if (!canSend) return;
    send(draft);
    setDraft("");
  }

  if (threadId === null) {
    return (
      <div
        className={`flex-1 items-center justify-center text-on-surface-variant text-sm ${className}`}
      >
        一覧から会話を選んでください。
      </div>
    );
  }

  return (
    <div className={`flex-1 flex-col gap-md md:pl-md ${className}`}>
      <header className="flex flex-col gap-xs border-outline-variant border-b pb-sm">
        {/* Below `md` the two panes swap instead of sitting side by side, so the
            conversation needs its own way back to the list (#254 の流儀に合わせる). */}
        <button
          type="button"
          onClick={onBack}
          className="-ml-xs self-start rounded-md px-xs py-1 text-on-surface-variant text-sm transition-colors hover:bg-surface-container-low md:hidden"
        >
          ← 一覧へ戻る
        </button>
        <h2 className="font-bold text-on-surface">
          {detail?.counterpart.name ?? (phase === "loading" ? "読み込み中…" : "不明な相手")}
        </h2>
        {detail?.question_title ? (
          <p className="line-clamp-1 text-on-surface-variant text-xs">{detail.question_title}</p>
        ) : null}
      </header>

      {phase === "error" ? (
        <p role="alert" className="text-on-surface-variant text-sm">
          この会話の取得に失敗しました。時間をおいて再度お試しください。
        </p>
      ) : (
        <ul className="flex min-h-[12rem] flex-1 flex-col gap-sm overflow-y-auto">
          {detail?.messages.length ? (
            detail.messages.map((message) => (
              <ChatBubble
                key={message.id}
                message={message}
                mine={message.sender_id === currentUserId}
              />
            ))
          ) : (
            <li className="text-center text-on-surface-variant text-sm">
              まだメッセージはありません。最初のメッセージを送ってみましょう。
            </li>
          )}
        </ul>
      )}

      <div className="flex flex-col gap-xs border-outline-variant border-t pt-sm">
        {sendError ? (
          <p role="alert" className="text-error text-xs">
            {sendError}
          </p>
        ) : null}
        <div className="flex items-end gap-sm">
          <textarea
            aria-label="メッセージを入力"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="メッセージを入力..."
            className="h-16 flex-1 resize-none rounded-lg border border-outline-variant bg-surface p-sm text-on-surface text-sm outline-none focus:border-primary"
          />
          <button
            type="button"
            disabled={!canSend}
            onClick={handleSend}
            className="inline-flex min-h-[40px] items-center rounded-lg bg-primary px-md py-2 font-bold text-on-primary text-sm shadow-sm transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
          >
            送信
          </button>
        </div>
      </div>
    </div>
  );
}

export function ChatScreen({ initialThreadId, initialSlackResult }: ChatScreenProps) {
  const router = useRouter();
  const { currentUserId } = useCurrentUser();
  const { phase, threads } = useChatThreads(currentUserId);
  const [selectedId, setSelectedId] = useState<number | null>(
    initialThreadId ? Number(initialThreadId) : null,
  );
  const [slackResult, setSlackResult] = useState(initialSlackResult ?? null);

  // Default to the most recently active thread once the list first loads, but
  // never override a deep link or an explicit selection the user already made.
  useEffect(() => {
    if (selectedId === null && threads.length > 0) {
      setSelectedId(threads[0].thread_id);
    }
  }, [threads, selectedId]);

  // Drop `?slack=linked|error` from the URL once shown, so a reload doesn't
  // re-show the one-off OAuth-result banner (#slack-integration).
  // biome-ignore lint/correctness/useExhaustiveDependencies: only meant to run once, on the redirect back from Slack.
  useEffect(() => {
    if (!initialSlackResult) return;
    router.replace(initialThreadId ? `/chat?thread=${initialThreadId}` : "/chat");
  }, []);

  // Below `md` there is no room for two panes, so they take turns: the list
  // until a thread is picked, then the conversation with a way back (#254).
  const [showConversation, setShowConversation] = useState(initialThreadId != null);

  return (
    // The height that used to be pinned on <section> alone now lives here: the
    // SlackLinkButton row (and the OAuth-result banner, when shown) sit ABOVE
    // it and take real space, so <section> can no longer assume a fixed
    // 100dvh-9rem for itself — it now grows to fill whatever this outer
    // column has left (`min-h-0 flex-1` below), whatever that row's height is.
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-md py-lg md:h-[calc(100dvh-9rem)]">
      <div className="flex items-center justify-end gap-sm">
        <SlackLinkButton />
      </div>
      {slackResult ? (
        <output
          className={`rounded-lg px-sm py-xs text-sm ${
            slackResult === "linked"
              ? "bg-secondary-container text-on-secondary-container"
              : "bg-error-container text-on-error-container"
          }`}
        >
          <span>
            {slackResult === "linked"
              ? "Slackと連携しました。"
              : "Slack連携に失敗しました。時間をおいて再度お試しください。"}
          </span>
          <button type="button" onClick={() => setSlackResult(null)} className="ml-sm underline">
            閉じる
          </button>
        </output>
      ) : null}
      {/* Only the DESKTOP layout bounds the height so the two panes scroll
          independently (`min-h-0` lets a flex child shrink below its content's
          natural height, which `overflow-y-auto` further down needs to ever
          kick in instead of just growing). At phone width the wrapping div
          above has no fixed height either, so this grows naturally and the
          page scrolls instead — any fixed offset there pushed the composer
          off-screen. */}
      <section className="flex min-h-0 flex-col gap-md md:flex-1 md:flex-row">
        <ChatThreadList
          threads={threads}
          phase={phase}
          selectedId={selectedId}
          onSelect={(id) => {
            setSelectedId(id);
            setShowConversation(true);
          }}
          className={showConversation ? "hidden md:flex" : "flex"}
        />
        <ChatConversation
          threadId={selectedId}
          currentUserId={currentUserId}
          onBack={() => setShowConversation(false)}
          className={showConversation ? "flex" : "hidden md:flex"}
        />
      </section>
    </div>
  );
}
