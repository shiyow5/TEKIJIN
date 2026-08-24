"use client";

/**
 * A "自分で解決した" control for one still-pending past question (#159).
 *
 * The self-resolution UX signal: the asker got what they needed (a document, a
 * past answer, or just thinking it through) WITHOUT asking a person, and says so.
 * Shown only on pending items. A first click asks for confirmation (so it is not
 * fired by accident); the second POSTs `resolve`. On success the parent marks the
 * item self-resolved; on failure an error marker is shown and the row stays.
 * Meant to sit as a sibling of a card's `Link`, never nested, so it cannot
 * navigate.
 */

import { resolveQuestion } from "@/lib/api-client";
import { useState } from "react";

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

  if (phase === "confirm") {
    return (
      <div className="flex items-center gap-xs rounded-full border border-outline-variant bg-surface-container-highest px-xs py-[2px] shadow-sm">
        <span className="text-on-surface text-xs">自分で解決しましたか？</span>
        <button
          type="button"
          onClick={handleResolve}
          className="rounded-full bg-primary px-xs py-[1px] font-bold text-on-primary text-xs"
        >
          解決済みにする
        </button>
        <button
          type="button"
          onClick={() => setPhase("idle")}
          className="rounded-full px-xs py-[1px] text-on-surface-variant text-xs hover:underline"
        >
          やめる
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      disabled={phase === "resolving"}
      onClick={() => setPhase("confirm")}
      aria-label={`「${title}」を自分で解決済みにする`}
      className="rounded-full border border-primary px-sm py-[2px] text-primary text-xs transition-colors hover:bg-surface-container-low disabled:opacity-50"
    >
      {phase === "resolving" ? "…" : phase === "error" ? "再試行" : "自分で解決した"}
    </button>
  );
}
