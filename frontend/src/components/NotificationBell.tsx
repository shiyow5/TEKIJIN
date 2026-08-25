"use client";

/**
 * Decline-notification bell in the header (#E7).
 *
 * Paired with the automatic reroute (#206): when a candidate declines, the
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
import { useEffect, useLayoutEffect, useRef, useState } from "react";

const POLL_INTERVAL_MS = 15_000;
const PANEL_WIDTH_PX = 320;
const VIEWPORT_MARGIN_PX = 8;

export function NotificationBell() {
  const { currentUserId } = useCurrentUser();
  const [items, setItems] = useState<DeclineNotification[]>([]);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // Panel left offset (relative to `containerRef`), clamped to the viewport.
  // The bell's on-screen position depends on where the header happens to wrap
  // (driven by real content width, not a fixed breakpoint — #316), so a static
  // `left-0`/`right-0` class can put the panel off either edge depending on
  // viewport width and header content. Measuring on open and clamping is the
  // only way to keep it on-screen regardless of where the bell lands.
  const [panelLeft, setPanelLeft] = useState<number | null>(null);
  // Rendered via inline `width` (not a `w-80` Tailwind class) so this always
  // matches the value the clamp math above used — a `rem`-based class can
  // render wider than `PANEL_WIDTH_PX` under text-only zoom (larger root font
  // size), which would silently reintroduce the #316 overflow.
  const [panelWidth, setPanelWidth] = useState(PANEL_WIDTH_PX);

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

  // Runs before paint so the panel never flashes at an unclamped position.
  useLayoutEffect(() => {
    if (!open) return;
    function reposition() {
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const computedWidth = Math.max(
        0,
        Math.min(PANEL_WIDTH_PX, window.innerWidth - VIEWPORT_MARGIN_PX * 2),
      );
      // Default: right-align the panel under the bell (its usual position when
      // the bell sits near the header's right edge); clamp into the viewport
      // when that would run off either side.
      const maxViewportLeft = window.innerWidth - computedWidth - VIEWPORT_MARGIN_PX;
      const viewportLeft = Math.max(
        VIEWPORT_MARGIN_PX,
        Math.min(rect.right - computedWidth, maxViewportLeft),
      );
      setPanelLeft(viewportLeft - rect.left);
      setPanelWidth(computedWidth);
    }

    // Coalesce to at most once per frame — a window resize fires far more
    // often than the layout actually settles.
    let rafId: number | null = null;
    function scheduleReposition() {
      if (rafId !== null) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        reposition();
      });
    }

    reposition();
    window.addEventListener("resize", scheduleReposition);
    // The header can rewrap (moving the bell) from something other than a
    // viewport resize — e.g. the web font swapping in and changing sibling
    // label widths — which never fires `resize`. Watching the <header> itself
    // catches that: its height changes whenever the wrap count does.
    // Guarded: not implemented in the test environment (jsdom) or in older
    // browsers/webviews, and this repositioning is a nice-to-have on top of
    // the `resize`-driven reposition, not the only way it happens.
    const header = containerRef.current?.closest("header") ?? null;
    const observer =
      header && typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(scheduleReposition)
        : null;
    observer?.observe(header as Element);

    return () => {
      window.removeEventListener("resize", scheduleReposition);
      observer?.disconnect();
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
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
          // `left` is computed in the layout effect above and clamped to the
          // viewport (#316) — where the bell lands on screen depends on real
          // header content width, not a fixed breakpoint, so a static
          // left-0/right-0 class can't stay correct at every width.
          style={{
            width: `${panelWidth}px`,
            ...(panelLeft !== null ? { left: `${panelLeft}px` } : {}),
          }}
          className="absolute z-10 mt-xs rounded-lg border border-outline-variant bg-surface-container-lowest p-xs shadow-md"
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
