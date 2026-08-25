"use client";

/**
 * Processing / thinking-progress screen (product-spec 画面2 / F-08).
 *
 * Subscribes to GET /events/{id} via `useEventStream` and renders the AI's
 * progress step by step: 質問理解 -> 経路判断 -> 候補推定 -> 依頼文作成. Each
 * received event becomes a completed step (✓); while the run is still going an
 * in-progress step (⟳) is shown. A `followup` interrupt shows a reply box that
 * POSTs /answer and lets the stream resume. On `recommend`/`done` the user can
 * move to the result screen; `message` and `error` are terminal displays.
 */

import { FollowupForm } from "@/components/FollowupForm";
import { PageBackLink } from "@/components/PageBackLink";
import { useOptionalSessionStream } from "@/components/SessionStreamProvider";
import { type EventStreamState, useEventStream } from "@/hooks/useEventStream";
import { ApiError, postAnswer } from "@/lib/api-client";
import { formatConfidence } from "@/lib/format";
import { routeLabel } from "@/lib/routes";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export interface ProcessingScreenProps {
  sessionId: string;
  /** Test seam: inject a pre-built stream state instead of subscribing. */
  streamState?: EventStreamState;
  /** Test seam: inject the EventSource constructor for the live subscription. */
  eventSourceFactory?: (url: string) => EventSource;
  /** Test seam: override the API base URL for the live subscription. */
  baseUrl?: string;
}

interface Step {
  id: string;
  title: string;
  details: string[];
  confidence?: number;
}

const FOLLOWUP_ERROR = "回答の送信に失敗しました。もう一度お試しください。";

function joinDomain(topics: string[], products: string[]): string {
  const parts = [...topics, ...products].filter((p) => p.trim() !== "");
  return parts.length > 0 ? parts.join(" / ") : "—";
}

/** Derive the ordered, completed steps from the accumulated stream state. */
function buildSteps(stream: EventStreamState): Step[] {
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

/**
 * True once the stream has moved past the most recent `followup` (a later event
 * arrived). Treated as a success signal so the reply box closes even if the
 * /answer ack was lost — the run has demonstrably resumed.
 */
function isFollowupSuperseded(stream: EventStreamState): boolean {
  const names = stream.events.map((e) => e.event);
  const lastFollowup = names.lastIndexOf("followup");
  if (lastFollowup === -1) {
    return false;
  }
  return names.length - 1 > lastFollowup;
}

export function ProcessingScreen({
  sessionId,
  streamState,
  eventSourceFactory,
  baseUrl,
}: ProcessingScreenProps) {
  const router = useRouter();
  const contextStream = useOptionalSessionStream();
  // Prefer an injected state (test seam), then the shared provider context
  // (production, mounted by the route layout), then a self-owned subscription
  // (kept only for the standalone test seam — disabled when context is present).
  const liveStream = useEventStream(sessionId, {
    enabled: streamState === undefined && contextStream === null,
    eventSourceFactory,
    baseUrl,
  });
  const stream = streamState ?? contextStream ?? liveStream;

  const [answered, setAnswered] = useState(false);
  const [followupSubmitting, setFollowupSubmitting] = useState(false);
  const [followupError, setFollowupError] = useState<string | null>(null);

  // A newly arrived followup re-opens the reply box.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset only when a new followup arrives.
  useEffect(() => {
    setAnswered(false);
    setFollowupError(null);
  }, [stream.followup]);

  const steps = buildSteps(stream);
  const hasRecommendations = (stream.recommend?.recommendations.length ?? 0) > 0;
  // A draft implies a recommendation was produced: on a refresh at the `send`
  // interrupt the reconnect replays only the draft, so treat it as result access.
  const hasResult = hasRecommendations || Boolean(stream.draft?.draft) || Boolean(stream.done);
  // Hide the reply box once answered, once the run terminates, or once the
  // stream has advanced past the followup (ack-loss recovery).
  const showFollowup =
    Boolean(stream.followup) && !answered && !stream.terminal && !isFollowupSuperseded(stream);
  // Stop the "分析を続けています…" spinner once a result is ready: the person route
  // pauses (non-terminal) at the `send` interrupt, so without this the spinner
  // spins forever even though analysis is done and the result is waiting (#148).
  const showActiveStep = !stream.terminal && !stream.error && !showFollowup && !hasResult;

  async function handleReply(reply: string) {
    setFollowupSubmitting(true);
    setFollowupError(null);
    try {
      await postAnswer({ session_id: sessionId, reply });
      setAnswered(true);
    } catch (err) {
      // 409 = the run was already resumed (a lost/duplicate ack); treat as
      // success and close the form rather than showing an error.
      if (err instanceof ApiError && err.status === 409) {
        setAnswered(true);
      } else {
        setFollowupError(FOLLOWUP_ERROR);
      }
    } finally {
      setFollowupSubmitting(false);
    }
  }

  function goToResult() {
    router.push(`/session/${sessionId}/result`);
  }

  let heading = "最適な回答者を探しています…";
  if (stream.error) {
    heading = "エラーが発生しました";
  } else if (stream.message) {
    heading = "回答をお届けします";
  } else if (hasResult) {
    heading = "回答者が見つかりました";
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-lg py-lg">
      <PageBackLink href="/questions" label="質問一覧へ戻る" className="-mb-sm" />
      <header className="text-center">
        <h1 className="flex items-center justify-center gap-sm font-bold text-2xl text-primary">
          {showActiveStep ? (
            <span aria-hidden="true" className="animate-spin text-xl motion-reduce:animate-none">
              ⟳
            </span>
          ) : null}
          {heading}
        </h1>
        <p className="mt-sm text-on-surface-variant">
          AIが社内の知識ネットワークを分析し、最も詳しい人物を特定しています。
        </p>
      </header>

      <section
        aria-label="AIの思考プロセス"
        aria-live="polite"
        aria-atomic="false"
        className="flex flex-col gap-md"
      >
        <ol className="flex flex-col gap-sm">
          {steps.map((step) => (
            <li
              key={step.id}
              className="flex items-start gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md"
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
              className="flex items-center gap-sm rounded-xl border border-primary-fixed bg-surface-container-low p-md"
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

        {showFollowup && stream.followup ? (
          <FollowupForm
            question={stream.followup.question}
            missing={stream.followup.missing}
            submitting={followupSubmitting}
            errorMessage={followupError}
            onReply={handleReply}
          />
        ) : null}

        {stream.message ? (
          <div className="rounded-xl border border-outline-variant bg-surface-container-low p-md">
            <p className="text-on-surface">
              {stream.message.message || "該当する回答が見つかりませんでした。"}
            </p>
            {stream.message.doc_id ? (
              <Link
                href={`/documents/${encodeURIComponent(stream.message.doc_id)}?from=${encodeURIComponent(sessionId)}`}
                className="mt-sm inline-flex min-h-[44px] items-center gap-xs rounded-full bg-primary px-md py-sm font-bold text-on-primary transition-colors hover:bg-primary-container"
              >
                <span aria-hidden="true">📄</span>
                文書を見る
              </Link>
            ) : null}
            {stream.message.citations && stream.message.citations.length > 0 ? (
              <div className="mt-md">
                <p className="text-xs font-bold text-on-surface-variant">出典</p>
                <ul className="mt-xs flex flex-wrap gap-xs">
                  {stream.message.citations.map((citation) => (
                    <li key={`${citation.kind}:${citation.source_id}`}>
                      {citation.kind === "document" ? (
                        <Link
                          href={`/documents/${encodeURIComponent(citation.source_id)}?from=${encodeURIComponent(sessionId)}`}
                          className="inline-flex min-h-[44px] items-center gap-xs rounded-full border border-outline-variant bg-surface px-md py-sm text-sm font-bold text-primary transition-colors hover:bg-surface-container"
                        >
                          <span aria-hidden="true">📄</span>
                          {citation.source_id}
                        </Link>
                      ) : (
                        // "qa" 出典は専用ビューアが未実装（ナレッジ詳細 #293 part2 で
                        // /knowledge/[id] を用意したらリンク化する）。今は非リンクのチップ。
                        <span className="inline-flex min-h-[44px] items-center gap-xs rounded-full border border-outline-variant bg-surface-container-low px-md py-sm text-sm text-on-surface-variant">
                          <span aria-hidden="true">💬</span>
                          過去の回答 {citation.source_id}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {stream.error ? (
          <div
            role="alert"
            className="rounded-xl border border-error-container bg-error-container p-md text-on-error-container"
          >
            処理中にエラーが発生しました。時間をおいて再度お試しください。
          </div>
        ) : null}
      </section>

      <footer className="flex flex-wrap justify-center gap-md">
        {hasResult ? (
          <button
            type="button"
            onClick={goToResult}
            className="min-h-[48px] rounded-full bg-primary px-lg py-sm font-bold text-on-primary shadow-md transition-colors hover:bg-primary-container"
          >
            結果を見る
          </button>
        ) : null}
        {stream.terminal || stream.error ? (
          <a
            href="/questions"
            className="min-h-[48px] rounded-full border border-outline px-lg py-sm text-on-surface-variant transition-colors hover:bg-surface-container-low"
          >
            新しい質問をする
          </a>
        ) : null}
      </footer>
    </div>
  );
}
