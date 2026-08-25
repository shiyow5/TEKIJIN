"use client";

/**
 * A "自分で解決した" control for one still-pending past question (#159).
 *
 * The self-resolution UX signal: the asker got what they needed (a document, a
 * past answer, or just thinking it through) WITHOUT asking a person, and says so.
 * Shown only on pending items. A first click opens a confirmation popup (so it
 * is not fired by accident); confirming there POSTs `resolve`. On success the
 * parent marks the item self-resolved; on failure an error marker is shown and
 * the row stays. Meant to sit as a sibling of a card's `Link`, never nested, so
 * it cannot navigate.
 */

import { resolveQuestion } from "@/lib/api-client";
import { useEffect, useId, useRef, useState } from "react";

function ResolveConfirmDialog({
  title,
  onConfirm,
  onCancel,
}: {
  title: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  // `aria-modal="true"` promises assistive tech that the rest of the page is
  // inert, so the focus contract has to hold: move focus in on open, keep Tab
  // inside, and hand it back to whatever opened us on close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    confirmButtonRef.current?.focus();
    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>("button");
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !dialogRef.current?.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-on-surface/40 p-md">
      {/* biome-ignore lint/a11y/useSemanticElements: role="dialog" + aria-modal
      matches this component's own onCancel/Escape handling; the native
      <dialog> element's imperative showModal()/close() API isn't needed here. */}
      <div
        role="dialog"
        ref={dialogRef}
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex w-full max-w-sm flex-col gap-md rounded-xl border border-outline-variant bg-surface-container-lowest p-lg shadow-md"
      >
        <h2 id={titleId} className="font-bold text-lg text-on-surface">
          自分で解決しましたか？
        </h2>
        <p className="text-on-surface-variant text-sm">「{title}」を解決済みとして記録します。</p>
        <div className="flex justify-end gap-sm">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md px-md py-sm text-on-surface-variant text-sm hover:underline"
          >
            やめる
          </button>
          <button
            ref={confirmButtonRef}
            type="button"
            onClick={onConfirm}
            className="rounded-md bg-primary px-md py-sm font-bold text-on-primary text-sm shadow-sm transition-colors hover:bg-primary-container"
          >
            解決済みにする
          </button>
        </div>
      </div>
    </div>
  );
}

export function QuestionResolveButton({
  questionId,
  title,
  onResolved,
}: {
  questionId: string;
  title: string;
  onResolved: (questionId: string) => void;
}) {
  const [phase, setPhase] = useState<"idle" | "confirm" | "resolving" | "error">("idle");

  async function handleResolve() {
    setPhase("resolving");
    try {
      await resolveQuestion(questionId);
      onResolved(questionId);
    } catch {
      setPhase("error");
    }
  }

  return (
    <>
      <button
        type="button"
        disabled={phase === "resolving" || phase === "confirm"}
        onClick={() => setPhase("confirm")}
        aria-label={`「${title}」を自分で解決済みにする`}
        className="rounded-full border border-primary px-sm py-[2px] text-primary text-xs transition-colors hover:bg-surface-container-low disabled:opacity-50"
      >
        {phase === "resolving" ? "…" : phase === "error" ? "再試行" : "自分で解決した"}
      </button>
      {phase === "confirm" ? (
        <ResolveConfirmDialog
          title={title}
          onConfirm={handleResolve}
          onCancel={() => setPhase("idle")}
        />
      ) : null}
    </>
  );
}
