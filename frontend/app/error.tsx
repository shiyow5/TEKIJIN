"use client";

import { useEffect } from "react";

/**
 * Route-level error boundary (#126). Catches render/data errors in a segment and
 * offers a retry, instead of an unhandled crash. The underlying error detail is
 * never shown to the user (no sensitive leakage) but IS logged to the console so
 * it is not silently swallowed; `reset()` re-renders the segment.
 */
export default function ErrorBoundary({
  error,
  reset,
}: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Log for diagnosis (no monitoring backend yet); never surfaced in the UI.
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col items-center gap-md py-lg text-center">
      <h1 className="font-bold text-2xl text-on-surface">問題が発生しました</h1>
      <p role="alert" className="text-on-surface-variant">
        画面の表示中にエラーが発生しました。時間をおいて再度お試しください。
      </p>
      <button
        type="button"
        onClick={reset}
        className="min-h-[48px] rounded-full bg-primary px-lg py-sm font-bold text-on-primary shadow-md transition-colors hover:bg-primary-container"
      >
        再読み込み
      </button>
    </div>
  );
}
