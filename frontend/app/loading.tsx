/**
 * Route-level loading fallback (#126). Shown during a route segment's suspense —
 * a slow server component or navigation no longer flashes a blank screen.
 */
export default function Loading() {
  return (
    <div
      className="mx-auto flex w-full max-w-3xl items-center justify-center py-lg"
      aria-busy="true"
    >
      <p role="status" className="text-on-surface-variant">
        読み込み中…
      </p>
    </div>
  );
}
