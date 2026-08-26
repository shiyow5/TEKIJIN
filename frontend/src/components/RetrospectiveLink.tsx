"use client";

/**
 * "ふりかえりを記録する" CTA, shown on a finished session's result view (#247).
 *
 * Self-contained on purpose: it fetches the hand-off itself and renders NOTHING
 * unless the asker chose 直接相談. That keeps `ResultScreen`'s data flow (pure
 * SSE stream state) untouched, and puts the "is this a direct consultation?"
 * decision somewhere it can be tested on its own.
 *
 * Every failure path is silent. This is an optional prompt on a screen that is
 * already complete; a lookup error must not turn it into an error screen.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { getHandoff } from "@/lib/api-client";

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
    getHandoff(sessionId)
      .then((data) => {
        if (!cancelled && data.consult_method === "direct" && data.question_id) {
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
