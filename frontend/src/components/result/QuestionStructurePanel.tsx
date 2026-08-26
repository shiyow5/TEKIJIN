"use client";

/**
 * On-demand "質問を整理する" panel for the person route (#475 Screen 01).
 *
 * The new-hire's real barrier to asking is psychological ("こんな初歩的なことを聞いて
 * いいのか"), so before sending the hand-off the asker can ask the AI to reshape their
 * raw question into the four fields a responder needs — 起きていること / 環境 /
 * 試したこと / 詰まっている点. This is GENERATED ON DEMAND (POST /handoff/structure),
 * never in the C1 auto-flow: the endpoint runs OUTSIDE the graph, so it cannot slow
 * the streamed result ([[tekijin-latency-and-streaming]]: C1 1.5s is frozen).
 *
 * The model leaves a field it cannot infer EMPTY rather than invent one (a fabricated
 * 環境 would mislead the responder); the fields are editable so the asker fills the
 * blanks and corrects the rest. "下書きに反映" folds the (edited) fields into the
 * hand-off draft, from where the normal send flow takes over — this panel never
 * sends anything itself.
 *
 * The fields reveal with the shared staggered entrance (motion.ts / REVEAL_CLASS),
 * keeping the full content in the DOM (a11y + content-presence safe).
 */

import { ApiError, structureQuestion } from "@/lib/api-client";
import { REVEAL_CLASS, revealStyle } from "@/lib/motion";
import { useRef, useState } from "react";

export interface QuestionStructurePanelProps {
  sessionId: string;
  disabled?: boolean;
  /** Fold the composed, edited fields into the hand-off draft. */
  onApply: (composedText: string) => void;
}

interface Fields {
  summary: string;
  environment: string;
  tried: string;
  blocker: string;
}

const EMPTY: Fields = { summary: "", environment: "", tried: "", blocker: "" };

/** Field order + labels + a fill-in hint for empty fields (the four hand-off fields). */
const FIELD_META: { key: keyof Fields; label: string; placeholder: string }[] = [
  { key: "summary", label: "起きていること", placeholder: "何が起きているかを一言で" },
  { key: "environment", label: "環境", placeholder: "OS・バージョン・利用サービスなど" },
  { key: "tried", label: "試したこと", placeholder: "すでに試したことがあれば" },
  { key: "blocker", label: "詰まっている点", placeholder: "何が分からず止まっているか" },
];

const STRUCTURE_ERROR = "質問の整理に失敗しました。時間をおいて、もう一度お試しください。";
const NO_QUESTION_ERROR = "整理できる質問が見つかりませんでした。";

/** Compose the non-empty fields into a labeled block for the draft. */
export function composeStructuredDraft(fields: Fields): string {
  return FIELD_META.map(({ key, label }) => ({ label, value: fields[key].trim() }))
    .filter(({ value }) => value.length > 0)
    .map(({ label, value }) => `【${label}】\n${value}`)
    .join("\n\n");
}

export function QuestionStructurePanel({
  sessionId,
  disabled = false,
  onApply,
}: QuestionStructurePanelProps) {
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [fields, setFields] = useState<Fields>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  async function handleStructure() {
    if (loading || disabled) return;
    setLoading(true);
    setError(null);
    try {
      const result = await structureQuestion({ session_id: sessionId });
      if (!mounted.current) return;
      setFields({
        summary: result.summary,
        environment: result.environment,
        tried: result.tried,
        blocker: result.blocker,
      });
      setOpen(true);
    } catch (err) {
      if (!mounted.current) return;
      // 404 = the session has no question yet (unknown / not started).
      setError(err instanceof ApiError && err.status === 404 ? NO_QUESTION_ERROR : STRUCTURE_ERROR);
    } finally {
      if (mounted.current) setLoading(false);
    }
  }

  function handleField(key: keyof Fields, value: string) {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  const composed = composeStructuredDraft(fields);

  return (
    <div className="rounded-xl border border-outline-variant border-dashed bg-surface-container-lowest p-md">
      {open ? (
        <div className="flex flex-col gap-sm">
          <p className="font-medium text-on-surface text-sm">
            質問を整理しました。空欄を埋めて、下書きに反映できます。
          </p>
          {FIELD_META.map(({ key, label, placeholder }, index) => (
            <div
              key={key}
              className={`flex flex-col gap-1 ${REVEAL_CLASS}`}
              style={revealStyle(index)}
            >
              <label htmlFor={`qs-${key}`} className="font-medium text-on-surface-variant text-xs">
                {label}
              </label>
              <textarea
                id={`qs-${key}`}
                value={fields[key]}
                onChange={(e) => handleField(key, e.target.value)}
                disabled={disabled}
                rows={2}
                maxLength={1000}
                placeholder={placeholder}
                className="w-full resize-none rounded-lg border border-outline-variant bg-surface p-sm text-on-surface text-sm outline-none focus:border-primary disabled:opacity-50"
              />
            </div>
          ))}
          <div className="flex justify-end gap-sm">
            <button
              type="button"
              onClick={() => setOpen(false)}
              disabled={disabled}
              className="min-h-[36px] rounded-lg px-md py-1 font-medium text-on-surface-variant text-sm transition-colors hover:text-on-surface disabled:opacity-50"
            >
              やめる
            </button>
            <button
              type="button"
              onClick={() => onApply(composed)}
              disabled={disabled || composed.length === 0}
              className="inline-flex min-h-[36px] items-center rounded-lg bg-secondary-container px-md py-1 font-medium text-on-secondary-container text-sm transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              下書きに反映
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-xs">
          <button
            type="button"
            onClick={handleStructure}
            disabled={loading || disabled}
            className="text-left font-medium text-on-surface-variant text-sm underline decoration-dotted underline-offset-2 transition-colors hover:text-on-surface disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "質問を整理しています…" : "AIに質問を整理してもらう"}
          </button>
          <p className="text-on-surface-variant text-xs">
            起きていること・環境・試したこと・詰まっている点に分けて、答える人が読みやすい形に整えます。
          </p>
        </div>
      )}

      {error ? (
        <p
          role="alert"
          className="mt-sm rounded-lg border border-error-container bg-error-container p-sm text-on-error-container text-sm"
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}
