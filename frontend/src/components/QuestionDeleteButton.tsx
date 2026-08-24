"use client";

/**
 * A two-step delete control for one past question (#207 / #208).
 *
 * Shared by the question screen's "最近のあなたの質問" panel and the history screen.
 * Meant to sit as a sibling of a card's ``Link`` (not nested inside it) so a click
 * never navigates. Deleting is not undoable, so the first click only asks for
 * confirmation; the second performs it. On success the parent drops the item; on
 * failure the row stays and an error marker is shown.
 */

import { deleteQuestion } from "@/lib/api-client";
import { useState } from "react";

export function QuestionDeleteButton({
  questionId,
  title,
  onDeleted,
}: {
  questionId: string;
  title: string;
  onDeleted: (questionId: string) => void;
}) {
  const [phase, setPhase] = useState<"idle" | "confirm" | "deleting" | "error">("idle");

  async function handleDelete() {
    setPhase("deleting");
    try {
      await deleteQuestion(questionId);
      onDeleted(questionId);
    } catch {
      setPhase("error");
    }
  }

  if (phase === "confirm") {
    return (
      <div className="absolute bottom-2 right-2 z-10 flex items-center gap-xs rounded-full border border-outline-variant bg-surface-container-highest px-xs py-[2px] shadow-sm">
        <span className="text-on-surface text-xs">削除しますか？</span>
        <button
          type="button"
          onClick={handleDelete}
          className="rounded-full bg-error px-xs py-[1px] font-bold text-on-error text-xs"
        >
          削除
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
      disabled={phase === "deleting"}
      onClick={() => setPhase("confirm")}
      aria-label={`「${title}」を削除`}
      className="absolute bottom-2 right-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-outline-variant bg-surface-container-highest text-on-surface-variant text-xs leading-none hover:bg-error-container hover:text-on-error-container disabled:opacity-50"
    >
      {phase === "deleting" ? "…" : phase === "error" ? "!" : "✕"}
    </button>
  );
}
