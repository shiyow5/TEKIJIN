/**
 * One past-question card (status chip + responder/document/pending footer).
 *
 * Shared by {@link RecentQuestions} (最近のあなたの質問, capped) and
 * {@link QuestionHistoryScreen} (すべて見る, #208/#F9) so the two views render
 * identically. `clickable` distinguishes a card that navigates to its session
 * result from a seeded history row with no session: only the former gets the
 * hover affordance, and the latter is marked 「履歴のみ」 so it does not look
 * pressable-but-dead (#179). `onDelete`, when given, adds a delete affordance
 * (#207/#F8).
 */

import type { RecentQuestionItem } from "@/lib/api-types";

/** First character of the responder's name, for the avatar chip. */
function avatarInitial(name: string): string {
  return name.slice(0, 1);
}

export interface QuestionCardProps {
  item: RecentQuestionItem;
  clickable: boolean;
  onDelete?: (item: RecentQuestionItem) => void;
  deleting?: boolean;
}

export function QuestionCard({ item, clickable, onDelete, deleting }: QuestionCardProps) {
  return (
    <article
      className={
        clickable
          ? "flex h-full flex-col rounded-xl border border-outline-variant bg-surface-container-lowest p-md transition-shadow hover:shadow-md"
          : "flex h-full flex-col rounded-xl border border-outline-variant border-dashed bg-surface-container-low p-md"
      }
    >
      <div className="mb-sm flex items-start justify-between gap-sm">
        <h3 className="font-bold text-lg text-on-surface">{item.title}</h3>
        <div className="flex items-center gap-xs">
          {clickable ? null : (
            <span className="whitespace-nowrap rounded-full bg-surface-container-high px-xs py-[2px] text-on-surface-variant text-xs">
              履歴のみ
            </span>
          )}
          <span
            className={
              item.resolved
                ? "whitespace-nowrap rounded-full bg-secondary-container px-xs py-[2px] text-on-secondary-container text-xs"
                : "whitespace-nowrap rounded-full border border-outline-variant bg-surface-container-low px-xs py-[2px] text-on-surface-variant text-xs"
            }
          >
            {item.resolved ? "解決済" : "対応中"}
          </span>
        </div>
      </div>
      {item.responder_name ? (
        <div className="mt-auto flex items-center gap-sm border-outline-variant border-t pt-sm">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary-container font-bold text-on-secondary-container text-sm">
            {avatarInitial(item.responder_name)}
          </div>
          <div className="flex flex-col">
            <span className="text-on-surface-variant text-xs">回答者</span>
            <span className="text-on-surface text-sm">{item.responder_name}</span>
          </div>
        </div>
      ) : item.resolution === "document" ? (
        <div className="mt-auto flex items-center gap-sm border-outline-variant border-t pt-sm text-on-surface-variant text-sm">
          <span aria-hidden="true">📄</span>
          <span>社内文書で回答</span>
        </div>
      ) : (
        <div className="mt-auto border-outline-variant border-t pt-sm text-on-surface-variant text-xs">
          取り次ぎ先を調整中です。
        </div>
      )}
      {onDelete ? (
        <button
          type="button"
          disabled={deleting}
          onClick={(e) => {
            // The card may be wrapped in a Link (clickable); the delete
            // affordance must never trigger that navigation.
            e.preventDefault();
            e.stopPropagation();
            onDelete(item);
          }}
          className="mt-sm self-end text-error text-xs transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-50"
        >
          {deleting ? "削除中…" : "削除する"}
        </button>
      ) : null}
    </article>
  );
}
