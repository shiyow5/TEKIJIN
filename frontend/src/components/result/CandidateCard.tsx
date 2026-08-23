/**
 * One recommended person on the main-line result (product-spec 画面3).
 *
 * Shows the person id ("E###"), name, department, the fit signal (`confidence`
 * = 高/中/低 — the intended user-facing signal), and the evidence `reasons`. The
 * raw `score` is a weighted internal ranking value (not a percentage) and is
 * never shown. The top-ranked card is `expanded` (full reason detail); lower
 * ranks show compact reason labels.
 */

import { ConfidenceGauge } from "@/components/result/ConfidenceGauge";
import type { Recommendation } from "@/lib/api-types";
import { reasonLabel } from "@/lib/reasons";

export interface CandidateCardProps {
  candidate: Recommendation;
  rank: number;
  expanded: boolean;
  selected: boolean;
  onSelect: (personId: string) => void;
}

function avatarInitial(name: string): string {
  return name.slice(0, 1);
}

export function CandidateCard({
  candidate,
  rank,
  expanded,
  selected,
  onSelect,
}: CandidateCardProps) {
  return (
    <article
      className={`flex h-full flex-col rounded-xl border bg-surface-container-lowest p-md shadow-sm transition-colors ${
        selected ? "border-primary" : "border-outline-variant"
      }`}
    >
      <div className="mb-sm flex items-center gap-sm">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-surface-variant font-bold text-lg text-on-surface-variant">
          {avatarInitial(candidate.name)}
        </div>
        <div className="flex min-w-0 flex-col">
          <h2 className="truncate font-bold text-lg text-on-surface leading-tight">
            {rank === 1 ? `${candidate.name}（最有力）` : candidate.name}
          </h2>
          <p className="truncate text-on-surface-variant text-xs">
            {[candidate.dept, candidate.person_id].filter(Boolean).join(" / ")}
          </p>
        </div>
        {/* Confidence gauge in normal flex flow (shrink-0) so it can never overlap
            the name — the label is aria-hidden since the gauge already labels it. */}
        <div className="ml-auto flex shrink-0 items-center gap-xs">
          <span aria-hidden="true" className="text-on-surface-variant text-xs">
            適合度
          </span>
          <ConfidenceGauge level={candidate.confidence} />
        </div>
      </div>

      {candidate.reasons.length > 0 ? (
        <ul className="mb-md flex flex-col gap-xs">
          {candidate.reasons.map((reason) => (
            <li
              key={`${reason.type}-${reason.detail}`}
              className="flex items-start gap-xs text-on-surface-variant text-sm"
            >
              <span aria-hidden="true" className="text-primary">
                ✓
              </span>
              <span>
                <span className="font-medium text-on-surface">{reasonLabel(reason.type)}</span>
                {expanded && reason.detail ? `：${reason.detail}` : null}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-md text-on-surface-variant text-sm">根拠を確認中…</p>
      )}

      <button
        type="button"
        onClick={() => onSelect(candidate.person_id)}
        aria-pressed={selected}
        className={`mt-auto min-h-[40px] rounded-lg px-md py-2 font-medium text-sm transition-colors ${
          selected
            ? "bg-primary text-on-primary"
            : "border border-primary text-primary hover:bg-surface-container-low"
        }`}
      >
        {selected ? "選択中" : "選択する"}
      </button>
    </article>
  );
}
