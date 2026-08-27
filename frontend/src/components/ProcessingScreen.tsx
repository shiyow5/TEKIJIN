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
import { ReferenceAnswer } from "@/components/ReferenceAnswer";
import {
  useOptionalSessionStream,
  useOptionalSessionStreamRestart,
} from "@/components/SessionStreamProvider";
import { SourceCitations } from "@/components/SourceCitations";
import { ThinkingSteps } from "@/components/ThinkingSteps";
import { type EventStreamState, useEventStream } from "@/hooks/useEventStream";
import { ApiError, postAnswer, requestDocumentFallback } from "@/lib/api-client";
import { REVEAL_CLASS } from "@/lib/motion";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
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

const FOLLOWUP_ERROR = "回答の送信に失敗しました。もう一度お試しください。";
const FALLBACK_ERROR = "候補者への取り次ぎを開始できませんでした。もう一度お試しください。";

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
  // A history-card click adds `?from=history` (HistoryScreen) so this screen —
  // and the result screen it forwards to — can send the user back to their
  // history list instead of the home hub (#397 follow-up).
  const fromHistory = useSearchParams().get("from") === "history";
  const resultHref = `/session/${sessionId}/result${fromHistory ? "?from=history" : ""}`;
  const contextStream = useOptionalSessionStream();
  const restartStream = useOptionalSessionStreamRestart();
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
  const [fallbackSubmitting, setFallbackSubmitting] = useState(false);
  const [fallbackError, setFallbackError] = useState<string | null>(null);

  // A newly arrived followup re-opens the reply box.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset only when a new followup arrives.
  useEffect(() => {
    setAnswered(false);
    setFollowupError(null);
  }, [stream.followup]);

  // #475 Screen 01: reassurance — how many OTHER people asked in this area. Hidden
  // at 0 (feature off / nobody else). Lowers the "is this a dumb question?" barrier.
  const similarAskerCount = stream.understood?.similar_asker_count ?? 0;
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
    router.push(resultHref);
  }

  async function handleDocumentFallback() {
    setFallbackSubmitting(true);
    setFallbackError(null);
    try {
      await requestDocumentFallback({ session_id: sessionId });
      restartStream?.();
      router.push(resultHref);
    } catch {
      setFallbackError(FALLBACK_ERROR);
    } finally {
      setFallbackSubmitting(false);
    }
  }

  let heading = "最適な回答者を探しています…";
  if (stream.error) {
    heading = "エラーが発生しました";
  } else if (stream.message?.status === "document") {
    heading = "関連する社内文書が見つかりました";
  } else if (stream.message) {
    heading = "回答をお届けします";
  } else if (hasResult) {
    heading = "回答者が見つかりました";
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-lg py-lg">
      <PageBackLink
        href={fromHistory ? "/history" : "/"}
        label={fromHistory ? "履歴へ戻る" : "ホームへ戻る"}
        className="-mb-sm"
      />
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
          {stream.message?.status === "document"
            ? "まずは社内文書をご確認ください。解決しない場合は候補者へ質問できます。"
            : "AIが社内の知識ネットワークを分析し、最も詳しい人物を特定しています。"}
        </p>
      </header>

      <section
        aria-label="AIの思考プロセス"
        aria-live="polite"
        aria-atomic="false"
        className="flex flex-col gap-md"
      >
        <ThinkingSteps stream={stream} showActiveStep={showActiveStep} />

        {/* #475 Screen 01: reassurance that the question is not unique — shown only
            when other askers exist (count ≥ 1), never at 0. */}
        {similarAskerCount >= 1 ? (
          <p
            className={`rounded-xl border border-outline-variant bg-surface-container-low p-md text-on-surface-variant text-sm ${REVEAL_CLASS}`}
          >
            <span aria-hidden="true" className="mr-xs">
              💬
            </span>
            同じ分野で、過去に{similarAskerCount}
            人が質問しています。あなただけではありません。
          </p>
        ) : null}

        {/* #413: additive cited answer, shown alongside the person hand-off flow. */}
        <ReferenceAnswer reference={stream.reference} sessionId={sessionId} />

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
            {stream.message.status === "document" && stream.message.fallback_responder ? (
              <button
                type="button"
                disabled={fallbackSubmitting}
                onClick={handleDocumentFallback}
                className="mt-sm ml-sm inline-flex min-h-[44px] items-center rounded-full border border-primary px-md py-sm font-bold text-primary transition-colors hover:bg-primary-fixed disabled:cursor-not-allowed disabled:opacity-60"
              >
                {fallbackSubmitting
                  ? "取り次ぎを準備しています…"
                  : `${stream.message.fallback_responder.name}さんに聞く`}
              </button>
            ) : null}
            {fallbackError ? (
              <p role="alert" className="mt-sm text-error text-sm">
                {fallbackError}
              </p>
            ) : null}
            <SourceCitations citations={stream.message.citations} sessionId={sessionId} />
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
            href="/"
            className="min-h-[48px] rounded-full border border-outline px-lg py-sm text-on-surface-variant transition-colors hover:bg-surface-container-low"
          >
            新しい質問をする
          </a>
        ) : null}
      </footer>
    </div>
  );
}
