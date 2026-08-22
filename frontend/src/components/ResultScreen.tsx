"use client";

/**
 * Result screen (product-spec 画面3) — reads the session SSE state from context
 * and branches on the `route`:
 *   - "prior_answer" -> auxiliary view (past answer as evidence of expertise),
 *     with "追加で聞く" dropping to the main line so it never dead-ends;
 *   - otherwise (person / unset) -> main line (candidate cards + sendable draft).
 *
 * Data comes from `SessionStreamProvider` (mounted in the route layout), so the
 * recommend/route/draft accumulated on the processing screen are available here.
 * A `streamState` prop is a test seam. Until results arrive, a safe "準備中"
 * placeholder is shown.
 */

import { PersonRouteView } from "@/components/result/PersonRouteView";
import { PriorAnswerView } from "@/components/result/PriorAnswerView";
import { useOptionalSessionStream } from "@/components/SessionStreamProvider";
import type { EventStreamState } from "@/hooks/useEventStream";
import { useState } from "react";

export interface ResultScreenProps {
  /** Test seam: provide a fixed stream state instead of reading context. */
  streamState?: EventStreamState;
}

const EMPTY_STREAM: EventStreamState = { events: [], terminal: false };

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
function ResultTerminal({
  done,
  message,
}: {
  done?: EventStreamState["done"];
  message?: EventStreamState["message"];
}) {
  const heading = message ? "回答をお届けします" : "依頼は送信済みです";
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
          href="/questions"
          className="min-h-[48px] rounded-full bg-primary px-lg py-sm font-bold text-on-primary shadow-md transition-colors hover:bg-primary-container"
        >
          新しい質問をする
        </a>
      </div>
    </section>
  );
}

export function ResultScreen({ streamState }: ResultScreenProps) {
  const contextStream = useOptionalSessionStream();
  const stream = streamState ?? contextStream ?? EMPTY_STREAM;

  const [forceMainLine, setForceMainLine] = useState(false);

  const recommendations = stream.recommend?.recommendations ?? [];
  const draft = stream.draft?.draft ?? "";
  const routeName = forceMainLine ? "person" : stream.route?.route;

  // A failed stream can no longer advance: surface it before any data gating.
  if (stream.error) {
    return <ResultError />;
  }
  // A terminal outcome wins over any retained route/recommend: a run that ended
  // (sent / no_candidate / off_topic), or a completed session replayed after a
  // hard reload, must show its outcome rather than a stale route or 準備中.
  if (stream.terminal && (stream.done || stream.message)) {
    return <ResultTerminal done={stream.done} message={stream.message} />;
  }

  const hasMainLineData = recommendations.length > 0 || draft !== "";
  const hasAnyData = hasMainLineData || Boolean(stream.route);
  if (!hasAnyData) {
    return <ResultPending />;
  }

  if (routeName === "prior_answer") {
    return (
      <PriorAnswerView
        answerer={recommendations[0]}
        reason={stream.route?.reason}
        canAskMore={hasMainLineData}
        onAskMore={() => setForceMainLine(true)}
      />
    );
  }

  if (hasMainLineData) {
    return (
      // Keyed by the top candidate so a decline->reroute (new recommend/draft for
      // a different person) remounts the whole view: it clears a stale "sent"
      // confirmation and the previous selection/edit, making the reroute
      // reachable. A same-recipient late draft keeps the key (edits preserved).
      <PersonRouteView
        key={recommendations[0]?.person_id ?? "no-candidate"}
        recommendations={recommendations}
        reason={stream.route?.reason}
        draft={draft}
      />
    );
  }

  return <ResultPending />;
}
