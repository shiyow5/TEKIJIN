"use client";

/**
 * "ふりかえりを記録する" CTA, shown on a finished session's result view (#247).
 *
 * Self-contained on purpose: it fetches the retrospective context itself and
 * renders NOTHING unless the asker chose 直接相談, someone accepted it, and no
 * write-up exists yet. That keeps `ResultScreen`'s data flow (pure SSE stream
 * state) untouched, and puts the "should we ask for a write-up?" decision
 * somewhere it can be tested on its own.
 *
 * It reads `GET /consult-retrospective/{session_id}`, NOT `GET /handoff`: the
 * hand-off view 404s once the responder records an outcome, so a CTA built on it
 * would only ever appear before the consultation had happened.
 *
 * Every failure path is silent. This is an optional prompt on a screen that is
 * already complete; a lookup error must not turn it into an error screen.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { getRetrospectiveContext } from "@/lib/api-client";

export interface RetrospectiveLinkProps {
  sessionId?: string;
}

export function RetrospectiveLink({ sessionId }: RetrospectiveLinkProps) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    let cancelled = false;
    getRetrospectiveContext(sessionId)
      .then((data) => {
        if (
          !cancelled &&
          data.consult_method === "direct" &&
          data.responder !== null &&
          !data.already_recorded
        ) {
          setShow(true);
        }
      })
      .catch(() => {
        /* optional CTA: stay silent */
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  if (!sessionId || !show) {
    return null;
  }

  return (
    <div className="mx-auto w-full max-w-md rounded-xl border border-outline-variant bg-surface-container-low p-md text-left">
      <p className="font-bold text-on-surface text-sm">相談のあとに、内容を残しませんか</p>
      <p className="mt-xs text-on-surface-variant text-sm">
        対面での相談は記録が残りません。書いておくと、次に同じことで困った人がたどり着けます。
      </p>
      <Link
        href={`/session/${encodeURIComponent(sessionId)}/retrospective`}
        className="mt-sm inline-flex min-h-[44px] items-center rounded-full bg-primary px-lg py-sm font-bold text-on-primary transition-colors hover:bg-primary-container"
      >
        ふりかえりを記録する
      </Link>
    </div>
  );
}
