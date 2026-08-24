"use client";

/**
 * Simple post-acceptance chat thread (#E6).
 *
 * Shown once a responder has accepted a hand-off, on both the asker's result
 * screen and the responder's answer screen, so the two can coordinate directly
 * instead of the flow ending at "we connected you." Delivery is a short poll
 * of `GET /messages` (no SSE/WebSocket channel exists for this — kept simple,
 * matching the "簡易メッセージ機能" scope) rather than real-time push.
 */

import { getMessages, postMessage } from "@/lib/api-client";
import type { MessageItem } from "@/lib/api-types";
import { useEffect, useRef, useState } from "react";

const POLL_INTERVAL_MS = 4_000;
const LOAD_ERROR = "メッセージを取得できませんでした。時間をおいて再度お試しください。";
const SEND_ERROR = "送信に失敗しました。時間をおいて、もう一度お試しください。";

export interface MessageThreadProps {
  sessionId: string;
  /** external "E###" form — this viewer's own id, to tell own vs. other messages apart. */
  currentUserId: string;
  /** The other participant's name, when known (best-effort — see #A1 open risk on stale SSE state). */
  otherPartyName?: string | null;
}

export function MessageThread({ sessionId, currentUserId, otherPartyName }: MessageThreadProps) {
  const [items, setItems] = useState<MessageItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let active = true;

    function poll() {
      getMessages(sessionId)
        .then((next) => {
          if (active) {
            setItems(next);
            setLoaded(true);
            setLoadError(false);
          }
        })
        .catch(() => {
          if (active) {
            setLoaded(true);
            setLoadError(true);
          }
        });
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [sessionId]);

  async function handleSend() {
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    setSendError(null);
    try {
      const message = await postMessage({ session_id: sessionId, sender_id: currentUserId, body });
      if (mounted.current) {
        setItems((prev) => [...prev, message]);
        setText("");
      }
    } catch {
      if (mounted.current) setSendError(SEND_ERROR);
    } finally {
      if (mounted.current) setSending(false);
    }
  }

  return (
    <section className="flex flex-col gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md text-left shadow-sm">
      <h2 className="font-bold text-on-surface text-sm">
        {otherPartyName ? `${otherPartyName}さんとのメッセージ` : "メッセージ"}
      </h2>

      <div className="flex max-h-64 flex-col gap-xs overflow-y-auto">
        {!loaded ? (
          <p className="text-on-surface-variant text-sm">読み込み中…</p>
        ) : loadError && items.length === 0 ? (
          <p className="text-on-surface-variant text-sm">{LOAD_ERROR}</p>
        ) : items.length === 0 ? (
          <p className="text-on-surface-variant text-sm">まだメッセージはありません。</p>
        ) : (
          items.map((item) => {
            const own = item.sender_id === currentUserId;
            return (
              <p
                key={item.id}
                className={`max-w-[80%] whitespace-pre-wrap rounded-lg px-sm py-xs text-sm ${
                  own
                    ? "ml-auto bg-primary text-on-primary"
                    : "mr-auto bg-surface-container text-on-surface"
                }`}
              >
                {item.body}
              </p>
            );
          })
        )}
      </div>

      {sendError ? (
        <p role="alert" className="text-error text-xs">
          {sendError}
        </p>
      ) : null}

      <div className="flex gap-xs">
        <label className="sr-only" htmlFor="message-thread-input">
          メッセージを入力
        </label>
        <textarea
          id="message-thread-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={sending}
          rows={2}
          className="h-16 flex-1 resize-none rounded-lg border border-outline-variant bg-surface p-sm text-on-surface text-sm outline-none focus:border-primary"
          placeholder="メッセージを入力…"
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={sending || text.trim() === ""}
          className="min-h-[40px] shrink-0 rounded-lg bg-primary px-md py-2 font-bold text-on-primary text-sm shadow-sm transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          送信
        </button>
      </div>
    </section>
  );
}
