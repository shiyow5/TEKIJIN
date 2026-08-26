"use client";

/**
 * "使い方" popover (#392), reached via a fixed bottom-right "？" button instead
 * of an always-visible strip — the 3-step content itself is unchanged, only how
 * it's surfaced. Built on {@link ModalDialog} for the same initial-focus /
 * focus-trap / Escape-to-close behavior as `ConsultMethodDialog` (#289).
 */

import { ModalDialog } from "@/components/ModalDialog";
import { useId, useRef, useState } from "react";

// One shared accent (primary-container) across all 3 cards — a uniform look
// rather than a 3-color triad.
const STEP_CARD_ACCENT = "border-primary-container/40 bg-primary-container/10";
const STEP_BADGE_ACCENT = "bg-primary-container text-on-primary-container";

const STEPS: { n: string; title: string; body: string }[] = [
  { n: "1", title: "質問を書く", body: "カテゴリ選択は不要。ふだんの言葉でそのまま。" },
  { n: "2", title: "AIが取り次ぐ", body: "社内の実績から、答えられそうな人を推薦。" },
  { n: "3", title: "人が答える", body: "AIが選んだ相手が、あなたに直接回答します。" },
];

export function HowItWorksButton() {
  const [open, setOpen] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="使い方を見る"
        className="fixed right-lg bottom-lg z-30 flex h-12 w-12 items-center justify-center rounded-full bg-primary font-bold text-on-primary text-xl shadow-lg transition-colors hover:bg-primary-container hover:text-on-primary-container"
      >
        ?
      </button>

      {open ? (
        <ModalDialog
          titleId={titleId}
          onCancel={() => setOpen(false)}
          initialFocusRef={closeButtonRef}
          dismissOnBackdrop
          maxWidthClassName="max-w-3xl"
        >
          <h2 id={titleId} className="font-bold text-lg text-on-surface">
            使い方
          </h2>
          <ol className="grid grid-cols-1 gap-md sm:grid-cols-3">
            {STEPS.map((step) => (
              <li
                key={step.n}
                className={`flex flex-col gap-xs rounded-xl border p-md shadow-sm ${STEP_CARD_ACCENT}`}
              >
                <span
                  className={`flex h-8 w-8 items-center justify-center rounded-full font-bold text-sm ${STEP_BADGE_ACCENT}`}
                >
                  {step.n}
                </span>
                <span className="font-bold text-on-surface text-sm">{step.title}</span>
                <span className="text-on-surface-variant text-sm leading-relaxed">{step.body}</span>
              </li>
            ))}
          </ol>
          {/* #337: today this 3-step flow is always "AI forwards, a person
              answers" (self_answer_enabled is still off). This note keeps the
              steps from reading as the only possible shape — so it doesn't
              quietly reintroduce the "answer source is always a person"
              claim #324 removed. */}
          <p className="text-on-surface-variant text-xs leading-relaxed">
            ※ 社内に知見が貯まるほど、一部はAIの直接回答に置き換わっていきます。
          </p>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={() => setOpen(false)}
            className="self-center text-on-surface-variant text-sm underline-offset-2 hover:underline"
          >
            閉じる
          </button>
        </ModalDialog>
      ) : null}
    </>
  );
}
