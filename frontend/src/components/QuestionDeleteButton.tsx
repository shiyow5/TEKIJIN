"use client";

/**
 * A delete control for one past question (#207 / #208), confirmed via a modal
 * dialog (#286) rather than an inline popover — a card list made the old
 * corner popover easy to miss, and an accidental delete is not undoable.
 *
 * Shared by the question screen's "最近のあなたの質問" panel and the history screen.
 * Meant to sit as a sibling of a card's ``Link`` (not nested inside it) so a click
 * never navigates. On success the parent drops the item; on failure the row
 * stays and an error marker is shown.
 */

import { QuestionDeleteDialog } from "@/components/QuestionDeleteDialog";
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

  return (
    <>
      {/* Not disabled while the dialog is open: ModalDialog's overlay and Tab
          trap already keep this button unreachable, and disabling it here
          would blur it before ModalDialog's opener-capture effect can run,
          breaking focus restoration on close. */}
      <button
        type="button"
        disabled={phase === "deleting"}
        onClick={() => setPhase("confirm")}
        aria-label={`「${title}」を削除`}
        className="absolute bottom-2 right-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-outline-variant bg-surface-container-highest text-on-surface-variant text-xs leading-none hover:bg-error-container hover:text-on-error-container disabled:opacity-50"
      >
        {phase === "deleting" ? "…" : phase === "error" ? "!" : "✕"}
      </button>
      {phase === "confirm" || phase === "deleting" ? (
        <QuestionDeleteDialog
          title={title}
          onConfirm={handleDelete}
          onCancel={() => setPhase("idle")}
          disabled={phase === "deleting"}
        />
      ) : null}
    </>
  );
}
