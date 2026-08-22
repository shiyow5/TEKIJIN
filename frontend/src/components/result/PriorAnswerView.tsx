"use client";

/**
 * Auxiliary result (route === "prior_answer"): a person is still the subject —
 * their past-answer record is shown as *evidence that they are the expert*, not
 * as the answer itself (the backend does not expose the answer body). "解決した"
 * completes; "この方に追加で聞く" drops down to the main line (取次ぎ) so the flow
 * never dead-ends — it is disabled when there is no main-line data to drop to.
 */

import type { Recommendation } from "@/lib/api-types";
import { answersEvidence } from "@/lib/reasons";
import { useState } from "react";

export interface PriorAnswerViewProps {
  answerer?: Recommendation;
  reason?: string;
  /** Whether the main line has data to drop to (candidates or a draft). */
  canAskMore?: boolean;
  onAskMore: () => void;
}

export function PriorAnswerView({
  answerer,
  reason,
  canAskMore = true,
  onAskMore,
}: PriorAnswerViewProps) {
  const [solved, setSolved] = useState(false);

  const name = answerer?.name ?? "詳しい方";
  const evidence = reason || "同様の質問に過去に回答しています。";
  // The past-answer record summary (count of past answers / useful ratio), taken
  // from the `answers` reason verbatim — not the answer text (we don't have it).
  const record = answerer ? answersEvidence(answerer.reasons) : null;

  if (solved) {
    return (
      <section className="mx-auto flex w-full max-w-2xl flex-col gap-md py-lg text-center">
        <h1 className="font-bold text-2xl text-secondary">解決しました</h1>
        <p className="text-on-surface-variant">お役に立てて何よりです。</p>
        <div className="flex justify-center">
          <a
            href="/questions"
            className="min-h-[48px] rounded-full bg-primary px-lg py-sm font-bold text-on-primary shadow-md transition-colors hover:bg-primary-container"
          >
            新しい質問をする
          </a>
        </div>
      </section>
    );
  }

  return (
    <section className="mx-auto flex w-full max-w-2xl flex-col gap-md py-lg">
      <header className="flex flex-col gap-xs">
        <h1 className="font-bold text-2xl text-on-surface">
          この質問には、{name}さんが詳しそうです
        </h1>
        <p className="inline-block rounded bg-surface-container px-sm py-1 text-on-surface-variant text-sm">
          証拠: {evidence}
        </p>
      </header>

      <div className="rounded-xl border border-outline-variant bg-surface-container-lowest p-md">
        <p className="mb-xs font-bold text-on-surface-variant text-xs">根拠（過去の回答実績）</p>
        <p className="text-on-surface text-sm">{record || "過去の回答実績を確認しています。"}</p>
      </div>

      <div className="flex flex-col gap-sm sm:flex-row">
        <button
          type="button"
          onClick={() => setSolved(true)}
          className="min-h-[48px] flex-1 rounded-lg bg-secondary px-md py-3 font-bold text-on-secondary shadow-sm transition-colors hover:bg-on-secondary-container"
        >
          解決した
        </button>
        <button
          type="button"
          onClick={onAskMore}
          disabled={!canAskMore}
          className="min-h-[48px] flex-1 rounded-lg border border-outline-variant px-md py-3 font-bold text-on-surface-variant transition-colors hover:bg-surface-container disabled:cursor-not-allowed disabled:opacity-50"
        >
          この方に追加で聞く
        </button>
      </div>
    </section>
  );
}
