"use client";

/**
 * Decline-notification bell in the header (#E7).
 *
 * Paired with the automatic reroute (#206/#D5): when a candidate declines, the
 * backend has already moved on to the next one by the time this fires — so
 * this is a "here's what happened" surface, not a request for the asker to
 * manually act. Polls `GET /notifications` for the acting user (no push/SSE
 * channel exists for this low-frequency, non-critical signal) and shows a
 * badge + dropdown; opening an item acknowledges it (`POST /notifications/ack`)
 * so it does not reappear.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import { ackNotifications, getNotifications } from "@/lib/api-client";
import type { DeclineNotification } from "@/lib/api-types";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

const POLL_INTERVAL_MS = 15_000;

export function NotificationBell() {
  const { currentUserId } = useCurrentUser();
  const [items, setItems] = useState<DeclineNotification[]>([]);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (currentUserId === null) {
      setItems([]);
      return;
    }
    let active = true;
    const askerId = currentUserId;

    function poll() {
      getNotifications(askerId)
        .then((next) => {
          if (active) setItems(next);
        })
        .catch(() => {
          // Best-effort background signal: a transient failure just means the
          // badge doesn't update this cycle, never a user-facing error.
        });
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [currentUserId]);

  useEffect(() => {
    if (!open) return;
    function onOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onOutside);
    return () => document.removeEventListener("mousedown", onOutside);
  }, [open]);

  function acknowledge(id: number) {
    setItems((prev) => prev.filter((item) => item.id !== id));
    if (currentUserId !== null) {
      ackNotifications({ asker_id: currentUserId, ids: [id] }).catch(() => {
        // Best-effort: a failed ack just means it may reappear on the next poll.
      });
    }
  }

  if (currentUserId === null) return null;

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={items.length > 0 ? `通知（未読${items.length}件）` : "通知"}
        className="relative flex h-9 w-9 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container-low"
      >
        <span aria-hidden="true">🔔</span>
        {items.length > 0 ? (
          <span
            aria-hidden="true"
            className="-top-1 -right-1 absolute flex h-4 min-w-[16px] items-center justify-center rounded-full bg-error px-[3px] font-bold text-[10px] text-on-error"
          >
            {items.length > 9 ? "9+" : items.length}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="通知一覧"
          className="absolute right-0 z-10 mt-xs w-80 max-w-[90vw] rounded-lg border border-outline-variant bg-surface-container-lowest p-xs shadow-md"
        >
          {items.length === 0 ? (
            <p className="p-sm text-on-surface-variant text-sm">新しい通知はありません。</p>
          ) : (
            <ul className="flex flex-col gap-xs">
              {items.map((item) => (
                <li key={item.id}>
                  {item.session_id ? (
                    <Link
                      href={`/session/${encodeURIComponent(item.session_id)}`}
                      onClick={() => {
                        acknowledge(item.id);
                        setOpen(false);
                      }}
                      className="block rounded-md p-sm text-on-surface text-sm transition-colors hover:bg-surface-container-low"
                    >
                      {item.message}
                    </Link>
                  ) : (
                    <div className="flex items-start justify-between gap-sm rounded-md p-sm text-on-surface text-sm">
                      <span>{item.message}</span>
                      <button
                        type="button"
                        onClick={() => acknowledge(item.id)}
                        className="shrink-0 text-primary text-xs hover:underline"
                      >
                        既読にする
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
