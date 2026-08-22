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

export function ResultScreen({ streamState }: ResultScreenProps) {
  const contextStream = useOptionalSessionStream();
  const stream = streamState ?? contextStream ?? EMPTY_STREAM;

  const [forceMainLine, setForceMainLine] = useState(false);

  const recommendations = stream.recommend?.recommendations ?? [];
  const draft = stream.draft?.draft ?? "";
  const routeName = forceMainLine ? "person" : stream.route?.route;

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
      <PersonRouteView
        recommendations={recommendations}
        reason={stream.route?.reason}
        draft={draft}
      />
    );
  }

  return <ResultPending />;
}
