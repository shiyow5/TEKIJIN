"use client";

/**
 * The AI's thinking-progress list (質問を理解しました → 経路を判断しました →
 * 候補を見つけました → 依頼文を作成しました).
 *
 * Shared by `ProcessingScreen` (live) and `ResultScreen` (#512): the steps used
 * to exist only on the processing screen, so returning to a session — from the
 * history list, from the result screen, or by reloading — produced a screen with
 * no progress on it, which reads as a different screen entirely. Rendering the
 * SAME component in both places is the point; a second, similar-looking list
 * would drift apart on the next change.
 *
 * Derived purely from the accumulated stream state, so it needs the backend to
 * replay `understood`/`route`/`recommend`/`draft` on reconnect (#512) — without
 * that the list is simply empty on a revisit.
 */

import type { EventStreamState } from "@/hooks/useEventStream";
import { formatConfidence } from "@/lib/format";
import { REVEAL_CLASS, revealStyle } from "@/lib/motion";
import { routeLabel } from "@/lib/routes";

export interface Step {
  id: string;
  title: string;
  details: string[];
  confidence?: number;
}

function joinDomain(topics: string[], products: string[]): string {
  const parts = [...topics, ...products].filter((p) => p.trim() !== "");
  return parts.length > 0 ? parts.join(" / ") : "—";
}

/** Derive the ordered, completed steps from the accumulated stream state. */
export function buildSteps(stream: EventStreamState): Step[] {
  const steps: Step[] = [];

  if (stream.understood) {
    const u = stream.understood;
    steps.push({
      id: "understood",
      title: "質問を理解しました",
      details: [
        `領域: ${joinDomain(u.topics, u.products)}`,
        ...(u.situation ? [`状況: ${u.situation}`] : []),
        ...(u.question_type ? [`種別: ${u.question_type}`] : []),
      ],
      confidence: u.confidence,
    });
  }

  if (stream.route) {
    steps.push({
      id: "route",
      title: "回答の経路を判断しました",
      details: [
        `経路: ${routeLabel(stream.route.route)}`,
        ...(stream.route.reason ? [stream.route.reason] : []),
      ],
      confidence: stream.route.confidence,
    });
  }

  // Only surface the recommend step when there is at least one candidate. The
  // backend emits an empty `recommend` before a terminal `no_candidate` message;
  // an empty result is left to that terminal message, not shown as a step/CTA.
  if (stream.recommend && stream.recommend.recommendations.length > 0) {
    const recs = stream.recommend.recommendations;
    steps.push({
      id: "recommend",
      title: `候補を${recs.length}名見つけました`,
      details: [recs.map((r) => r.name).join("、")],
    });
  }

  // Suppress an empty draft step (no content yet).
  if (stream.draft?.draft) {
    steps.push({
      id: "draft",
      title: "依頼文を作成しました",
      details: [stream.draft.draft],
    });
  }

  return steps;
}

export function ThinkingSteps({
  stream,
  /** Show the "分析を続けています…" spinner row (live runs only). */
  showActiveStep = false,
}: {
  stream: EventStreamState;
  showActiveStep?: boolean;
}) {
  const steps = buildSteps(stream);
  // Nothing to say yet: render nothing rather than an empty list box, so a
  // result screen for a session with no replayed progress looks unchanged.
  if (steps.length === 0 && !showActiveStep) {
    return null;
  }

  return (
    <ol className="flex flex-col gap-sm">
      {steps.map((step, index) => (
        <li
          key={step.id}
          style={revealStyle(index)}
          className={`flex items-start gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md ${REVEAL_CLASS}`}
        >
          <span
            aria-label="完了"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary font-bold text-on-primary text-sm"
          >
            ✓
          </span>
          <div className="flex flex-col gap-xs">
            <div className="flex flex-wrap items-center gap-sm">
              <h2 className="font-bold text-on-surface">{step.title}</h2>
              {step.confidence !== undefined ? (
                <span className="rounded-full bg-secondary-container px-sm py-[2px] text-on-secondary-container text-xs">
                  AIの解釈確信度 {formatConfidence(step.confidence)}
                </span>
              ) : null}
            </div>
            {step.details.map((detail) => (
              <p key={detail} className="text-on-surface-variant text-sm">
                {detail}
              </p>
            ))}
          </div>
        </li>
      ))}

      {showActiveStep ? (
        <li
          data-testid="active-step"
          className={`flex items-center gap-sm rounded-xl border border-primary-fixed bg-surface-container-low p-md ${REVEAL_CLASS}`}
        >
          <span
            aria-label="進行中"
            className="animate-spin text-lg text-primary motion-reduce:animate-none"
          >
            ⟳
          </span>
          <p className="text-on-surface-variant text-sm">分析を続けています…</p>
        </li>
      ) : null}
    </ol>
  );
}
