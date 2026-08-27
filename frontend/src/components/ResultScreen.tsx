"use client";

/**
 * Result screen (product-spec 画面3) — reads the session SSE state from context
 * and renders the main line (candidate cards + sendable draft) whenever there is
 * a candidate or draft to show, regardless of `route` ("person" or the
 * single-candidate "prior_answer" auxiliary route alike, #310): both already
 * carry the past-answer evidence in `recommendations[].reasons`, which
 * `CandidateCard` renders, so there is nothing route-specific left to show on a
 * separate screen.
 *
 * Data comes from `SessionStreamProvider` (mounted in the route layout), so the
 * recommend/route/draft accumulated on the processing screen are available here.
 * A `streamState` prop is a test seam. Until results arrive, a safe "準備中"
 * placeholder is shown.
 */

import { PageBackLink } from "@/components/PageBackLink";
import { RetrospectiveLink } from "@/components/RetrospectiveLink";
import { useOptionalSessionId, useOptionalSessionStream } from "@/components/SessionStreamProvider";
import { SourceCitations } from "@/components/SourceCitations";
import { ThinkingSteps } from "@/components/ThinkingSteps";
import { CandidateCard } from "@/components/result/CandidateCard";
import { PersonRouteView } from "@/components/result/PersonRouteView";
import type { EventStreamState } from "@/hooks/useEventStream";
import type { Recommendation } from "@/lib/api-types";
import { fitPercents } from "@/lib/fit";
import { useSearchParams } from "next/navigation";
import type { ReactNode } from "react";

export interface ResultScreenProps {
  /** Test seam: provide a fixed stream state instead of reading context. */
  streamState?: EventStreamState;
  /** Test seam: provide the session id instead of reading it from context. */
  sessionId?: string;
}

const EMPTY_STREAM: EventStreamState = { events: [], terminal: false };

function ResultFrame({
  stream,
  showProgress = true,
  children,
}: {
  /** Accumulated stream state, for the thinking progress above the result. */
  stream: EventStreamState;
  /**
   * Render the thinking steps above the content. Off for the hand-off main line
   * (see the call site): `PersonRouteView` already shows the route reason and an
   * EDITABLE draft, so the steps would repeat both — two drafts on one screen,
   * one of them editable, is worse than the inconsistency it fixes.
   */
  showProgress?: boolean;
  children: ReactNode;
}) {
  // `?from=history` (added by HistoryScreen, #397) sends the user back to their
  // history list instead of the home hub.
  const fromHistory = useSearchParams().get("from") === "history";
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col py-lg">
      <PageBackLink
        href={fromHistory ? "/history" : "/"}
        label={fromHistory ? "履歴へ戻る" : "ホームへ戻る"}
      />
      {/* #512: the same steps the processing screen shows, from the progress the
          backend replays on reconnect. Without this, a session reached from the
          history list (which links straight here, #470) or reloaded showed the
          outcome with no sign of how it was reached, and read as a different
          screen. `showActiveStep` stays off: the spinner belongs to a run still
          in flight, and this screen is reached after one. */}
      {showProgress ? (
        <section aria-label="AIの思考プロセス" className="mb-md">
          <ThinkingSteps stream={stream} />
        </section>
      ) : null}
      {children}
    </div>
  );
}

function ResultPending() {
  return (
    <section className="mx-auto flex w-full max-w-2xl flex-col gap-md py-lg text-center">
      <h1 className="font-bold text-2xl text-on-surface">結果を準備中…</h1>
      <p className="text-on-surface-variant">回答者候補の分析が終わるとここに表示されます。</p>
    </section>
  );
}

/**
 * Terminal outcome for a session hard-reloaded after it finished: the backend
 * replays only the stored `done` / `message` event (no route/recommend/draft to
 * hydrate), so branch on it here instead of dead-ending on the pending state.
 */
/**
 * Terminal heading by the `message` event's status (off_topic / document /
 * unresolved / no_candidate). Only `document` self-resolves with an answer, so
 * only it keeps "回答をお届けします"; the others must not promise a delivered
 * answer when the body says the request was out of scope or unmatched (#178).
 */
function terminalHeading(status: string): string {
  switch (status) {
    case "off_topic":
      return "対象外のご質問です";
    case "no_candidate":
      return "担当者が見つかりませんでした";
    case "unresolved":
      return "ご質問を特定できませんでした";
    default:
      return "回答をお届けします";
  }
}

function ResultTerminal({
  done,
  message,
  sessionId,
}: {
  done?: EventStreamState["done"];
  message?: EventStreamState["message"];
  sessionId?: string;
}) {
  const heading = message ? terminalHeading(message.status) : "依頼は送信済みです";
  const body =
    message?.message ||
    done?.answer ||
    (message
      ? "該当する回答が見つかりませんでした。"
      : "この依頼は送信済みです。返信があると通知でお知らせします。");
  return (
    <section className="mx-auto flex w-full max-w-2xl flex-col gap-md py-lg text-center">
      <h1 className="font-bold text-2xl text-on-surface">{heading}</h1>
      <p className="whitespace-pre-wrap text-on-surface-variant">{body}</p>
      {/* #291: a self-answered / document terminal carries the sources it cited;
          render them on reload replay too, not only on the live ProcessingScreen
          (#382 review) — otherwise a hard-reloaded auto-answer loses verifiability. */}
      <div className="mx-auto w-full max-w-md text-left">
        <SourceCitations citations={message?.citations} sessionId={sessionId} />
      </div>
      {/* #247: a 直接相談 leaves no transcript, so the only record of what was
          learned is what the asker writes down. Renders itself away for a chat
          hand-off (and on any lookup failure). */}
      <RetrospectiveLink sessionId={sessionId} />
      <div className="flex justify-center">
        <a
          href="/"
          className="min-h-[48px] rounded-full bg-primary px-lg py-sm font-bold text-on-primary shadow-md transition-colors hover:bg-primary-container"
        >
          新しい質問をする
        </a>
      </div>
    </section>
  );
}

/**
 * The candidates the run proposed, shown READ-ONLY under a completed outcome
 * (#520).
 *
 * The confidence label, the fit gauge and the evidence only ever existed on the
 * live main line, which a terminal outcome replaces — so once the request was
 * sent, "なぜこの人なのか" could no longer be answered, from the history list or
 * anywhere else. The data survives a reconnect since #512, so this is purely the
 * missing exit.
 *
 * No `onSelect` / `onExclude`: a hand-off that already happened cannot be
 * re-targeted, and `CandidateCard` renders display-only without them (its own
 * docstring calls that "a static/replayed view"). Nothing is marked `selected`
 * either — the stream carries who was RECOMMENDED, not who ultimately received
 * it, and highlighting the top card would assert something we cannot check.
 */
function ReplayedCandidates({ recommendations }: { recommendations: Recommendation[] }) {
  if (recommendations.length === 0) {
    return null;
  }
  const fitValues = fitPercents(recommendations);
  return (
    <section className="mt-lg flex flex-col gap-sm">
      <h2 className="font-bold text-lg text-on-surface">候補と根拠</h2>
      <p className="text-on-surface-variant text-sm">この質問に対してAIが提示した候補です。</p>
      <div className="grid grid-cols-1 gap-md md:grid-cols-3">
        {recommendations.map((candidate, index) => (
          <CandidateCard
            key={candidate.person_id}
            candidate={candidate}
            rank={index + 1}
            expanded={index === 0}
            selected={false}
            fitPercent={fitValues[index]}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * The stream failed (server `error` event or a permanently CLOSED connection).
 * `error` is deliberately non-terminal in the hook, so it is surfaced here
 * explicitly — otherwise the screen would stall on the pending placeholder that
 * can no longer advance. Generic text only (no server detail leaked).
 */
function ResultError() {
  return (
    <section className="mx-auto flex w-full max-w-2xl flex-col gap-md py-lg text-center">
      <h1 className="font-bold text-2xl text-on-surface">エラーが発生しました</h1>
      <div
        role="alert"
        className="rounded-xl border border-error-container bg-error-container p-md text-on-error-container"
      >
        時間をおいて再度お試しください。
      </div>
      <div className="flex justify-center">
        <a
          href="/"
          className="min-h-[48px] rounded-full bg-primary px-lg py-sm font-bold text-on-primary shadow-md transition-colors hover:bg-primary-container"
        >
          新しい質問をする
        </a>
      </div>
    </section>
  );
}

export function ResultScreen({ streamState, sessionId }: ResultScreenProps) {
  const contextStream = useOptionalSessionStream();
  const contextSessionId = useOptionalSessionId();
  const stream = streamState ?? contextStream ?? EMPTY_STREAM;
  const effectiveSessionId = sessionId ?? contextSessionId;

  const recommendations = stream.recommend?.recommendations ?? [];
  const draft = stream.draft?.draft ?? "";

  // A failed stream can no longer advance: surface it before any data gating.
  if (stream.error) {
    return (
      <ResultFrame stream={stream}>
        <ResultError />
      </ResultFrame>
    );
  }
  // A terminal outcome wins over any retained route/recommend: a run that ended
  // (sent / no_candidate / off_topic), or a completed session replayed after a
  // hard reload, must show its outcome rather than a stale route or 準備中.
  if (stream.terminal && (stream.done || stream.message)) {
    return (
      <ResultFrame stream={stream}>
        <ResultTerminal
          done={stream.done}
          message={stream.message}
          sessionId={effectiveSessionId ?? undefined}
        />
        {/* #520: the outcome alone does not say why this person. Renders itself
            away for a terminal that produced no candidates (off_topic /
            no_candidate), where a header with nothing under it would read as
            "the AI found people and is hiding them". */}
        <ReplayedCandidates recommendations={recommendations} />
      </ResultFrame>
    );
  }

  const hasMainLineData = recommendations.length > 0 || draft !== "";
  const hasAnyData = hasMainLineData || Boolean(stream.route);
  if (!hasAnyData) {
    return (
      <ResultFrame stream={stream}>
        <ResultPending />
      </ResultFrame>
    );
  }

  if (hasMainLineData) {
    return (
      // The main line is the richer rendering of the same last two steps
      // (candidates + draft) AND carries the route reason, so the step list would
      // duplicate it rather than add anything (#512).
      <ResultFrame stream={stream} showProgress={false}>
        {/* Keyed by the top candidate so a decline->reroute (new recommend/draft for
            a different person) remounts the whole view: it clears a stale "sent"
            confirmation and the previous selection/edit, making the reroute
            reachable. A same-recipient late draft keeps the key (edits preserved). */}
        <PersonRouteView
          key={recommendations[0]?.person_id ?? "no-candidate"}
          recommendations={recommendations}
          reason={stream.route?.reason}
          draft={draft}
          reference={stream.reference}
          sessionId={effectiveSessionId}
        />
      </ResultFrame>
    );
  }

  return (
    <ResultFrame stream={stream}>
      <ResultPending />
    </ResultFrame>
  );
}
