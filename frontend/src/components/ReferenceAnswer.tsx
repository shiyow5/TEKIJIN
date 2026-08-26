import { SourceCitations } from "@/components/SourceCitations";
import type { ReferenceData } from "@/lib/api-types";

/**
 * #413: the additive cited answer shown ALONGSIDE a person hand-off ("参考:
 * 過去の類似回答"). The backend surfaces this on the person route when a grounded
 * past answer exists — it never replaces the hand-off, so this renders as a
 * supporting block, not the main line. Shared by the live ProcessingScreen and
 * the PersonRouteView result so the two never drift (mirrors `SourceCitations`).
 */
export function ReferenceAnswer({
  reference,
  sessionId,
}: {
  reference?: ReferenceData;
  sessionId?: string | null;
}) {
  if (!reference || reference.answer.trim() === "") {
    return null;
  }
  return (
    <div className="rounded-xl border border-outline-variant border-dashed bg-surface-container-lowest p-md">
      <p className="flex items-center gap-xs font-bold text-on-surface-variant text-xs">
        <span aria-hidden="true">💡</span>
        参考: 過去の類似回答
      </p>
      <p className="mt-xs whitespace-pre-wrap text-on-surface text-sm">{reference.answer}</p>
      <p className="mt-xs text-on-surface-variant text-xs">
        ※ AIが過去の記録から自動でまとめた参考情報です。確実な回答は下記の担当者へ。
      </p>
      <SourceCitations citations={reference.citations} sessionId={sessionId ?? undefined} />
    </div>
  );
}
