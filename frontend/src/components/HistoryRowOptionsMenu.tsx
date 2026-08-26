"use client";

/**
 * "…" options menu for one history card (#397): replaces the separately
 * always-visible "✕削除" and "自分で解決した" controls with a single trigger
 * that opens a small dropdown offering 削除 (always) and 自分で解決した
 * (only while the question is still pending, #159). Choosing an option opens
 * the same confirmation modal the old standalone buttons used
 * (`QuestionDeleteDialog` / `QuestionResolveDialog`) and calls the same
 * `deleteQuestion`/`resolveQuestion` API — only the trigger UI changed, not
 * the delete/resolve logic itself.
 *
 * Meant to sit as a sibling of the card's `Link` (not nested inside it), same
 * as the buttons it replaces, so its clicks never navigate.
 */

import { QuestionDeleteDialog } from "@/components/QuestionDeleteDialog";
import { QuestionResolveDialog } from "@/components/QuestionResolveDialog";
import { deleteQuestion, resolveQuestion } from "@/lib/api-client";
import { useEffect, useRef, useState } from "react";

type Phase = "closed" | "open" | "confirm-delete" | "deleting" | "confirm-resolve" | "resolving";

const DELETE_ERROR = "削除に失敗しました。もう一度お試しください。";
const RESOLVE_ERROR = "解決の記録に失敗しました。もう一度お試しください。";

export function HistoryRowOptionsMenu({
  questionId,
  title,
  pending,
  onDeleted,
  onResolved,
}: {
  questionId: string;
  title: string;
  /** Only a still-pending question offers "自分で解決した" (#159). */
  pending: boolean;
  onDeleted: (questionId: string) => void;
  onResolved: (questionId: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("closed");
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  /**
   * Choosing a menuitem unmounts the dropdown (removing the focused menuitem
   * from the DOM) in the same render that mounts the confirmation `ModalDialog`.
   * `ModalDialog` captures `document.activeElement` as the opener to restore
   * focus to on close (see its docstring) — but a removed element resets focus
   * to `<body>` before that capture can happen, so the trigger must be refocused
   * synchronously first, while the menuitem is still what's focused.
   */
  function openDialog(next: "confirm-delete" | "confirm-resolve") {
    triggerRef.current?.focus();
    setPhase(next);
  }

  useEffect(() => {
    if (phase !== "open") return;
    function onOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setPhase("closed");
      }
    }
    document.addEventListener("mousedown", onOutside);
    return () => document.removeEventListener("mousedown", onOutside);
  }, [phase]);

  function openMenu() {
    setError(null);
    setPhase((p) => (p === "open" ? "closed" : "open"));
  }

  async function handleDelete() {
    setPhase("deleting");
    try {
      await deleteQuestion(questionId);
      onDeleted(questionId);
    } catch {
      setPhase("closed");
      setError(DELETE_ERROR);
    }
  }

  async function handleResolve() {
    setPhase("resolving");
    try {
      await resolveQuestion(questionId);
      onResolved(questionId);
    } catch {
      setPhase("closed");
      setError(RESOLVE_ERROR);
    }
  }

  return (
    <div ref={containerRef} className="absolute top-2 right-2 z-10">
      <button
        ref={triggerRef}
        type="button"
        onClick={openMenu}
        aria-expanded={phase === "open"}
        aria-haspopup="true"
        aria-label={`「${title}」の操作`}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-outline-variant bg-surface-container-highest text-on-surface-variant leading-none hover:bg-surface-container-low"
      >
        <span aria-hidden="true">⋯</span>
      </button>

      {phase === "open" ? (
        <div
          role="menu"
          aria-label={`「${title}」の操作メニュー`}
          className="absolute top-full right-0 z-10 mt-xs w-40 rounded-lg border border-outline-variant bg-surface-container-lowest p-xs shadow-md"
        >
          {pending ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => openDialog("confirm-resolve")}
              className="block w-full rounded-md p-sm text-left text-on-surface text-sm hover:bg-surface-container-low"
            >
              自分で解決した
            </button>
          ) : null}
          <button
            type="button"
            role="menuitem"
            onClick={() => openDialog("confirm-delete")}
            className="block w-full rounded-md p-sm text-left text-error text-sm hover:bg-surface-container-low"
          >
            削除
          </button>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="absolute top-full right-0 mt-xs w-40 text-error text-xs">
          {error}
        </p>
      ) : null}

      {phase === "confirm-delete" || phase === "deleting" ? (
        <QuestionDeleteDialog
          title={title}
          onConfirm={handleDelete}
          onCancel={() => setPhase("closed")}
          disabled={phase === "deleting"}
        />
      ) : null}

      {phase === "confirm-resolve" ? (
        <QuestionResolveDialog
          title={title}
          onConfirm={handleResolve}
          onCancel={() => setPhase("closed")}
        />
      ) : null}
    </div>
  );
}
