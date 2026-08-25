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

import { ModalDialog } from "@/components/ModalDialog";
import { resolveQuestion } from "@/lib/api-client";
import { useId, useRef, useState } from "react";

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
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

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
      {/* Not disabled while the dialog is open: ModalDialog's overlay and Tab
          trap already keep this button unreachable, and disabling it here
          would blur it before ModalDialog's opener-capture effect can run,
          breaking focus restoration on close. */}
      <button
        type="button"
        disabled={phase === "resolving"}
        onClick={() => setPhase("confirm")}
        aria-label={`「${title}」を自分で解決済みにする`}
        className="rounded-full border border-primary px-sm py-[2px] text-primary text-xs transition-colors hover:bg-surface-container-low disabled:opacity-50"
      >
        {phase === "resolving" ? "…" : phase === "error" ? "再試行" : "自分で解決した"}
      </button>
      {phase === "confirm" ? (
        <ModalDialog
          titleId={titleId}
          onCancel={() => setPhase("idle")}
          initialFocusRef={confirmButtonRef}
        >
          <h2 id={titleId} className="font-bold text-lg text-on-surface">
            自分で解決しましたか？
          </h2>
          <p className="text-on-surface-variant text-sm">「{title}」を解決済みとして記録します。</p>
          <div className="flex justify-end gap-sm">
            <button
              type="button"
              onClick={() => setPhase("idle")}
              className="rounded-md px-md py-sm text-on-surface-variant text-sm hover:underline"
            >
              やめる
            </button>
            <button
              ref={confirmButtonRef}
              type="button"
              onClick={handleResolve}
              className="rounded-md bg-primary px-md py-sm font-bold text-on-primary text-sm shadow-sm transition-colors hover:bg-primary-container"
            >
              解決済みにする
            </button>
          </div>
        </ModalDialog>
      ) : null}
    </>
  );
}
