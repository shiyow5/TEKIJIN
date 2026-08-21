/**
 * "最近あなたが解決した質問" list.
 *
 * Data is mocked for now (kept in this module) and injected via props so it can
 * be swapped for a real API source later without touching the markup. The cards
 * mirror the template's "Recent Solutions" section.
 */

export interface RecentQuestion {
  id: string;
  title: string;
  responderName: string;
  status: string;
}

/** Placeholder data until the recent-questions API exists. Immutable. */
export const MOCK_RECENT_QUESTIONS: readonly RecentQuestion[] = [
  {
    id: "rq-1",
    title: "UTMの移行時の注意点",
    responderName: "高梨さん",
    status: "解決済",
  },
  {
    id: "rq-2",
    title: "社内Wi-Fiの申請方法",
    responderName: "鈴木さん",
    status: "解決済",
  },
] as const;

export interface RecentQuestionsProps {
  items?: readonly RecentQuestion[];
}

/** First character of the responder's name, used for the avatar chip. */
function avatarInitial(name: string): string {
  return name.slice(0, 1);
}

export function RecentQuestions({ items = MOCK_RECENT_QUESTIONS }: RecentQuestionsProps) {
  return (
    <section className="mt-lg w-full">
      <h2 className="mb-md px-xs font-bold text-on-surface text-xl">最近あなたが解決した質問</h2>

      {items.length === 0 ? (
        <p className="px-xs text-on-surface-variant text-sm">まだ解決済みの質問はありません。</p>
      ) : (
        <ul className="grid grid-cols-1 gap-gutter md:grid-cols-2">
          {items.map((item) => (
            <li key={item.id}>
              <article className="flex h-full flex-col rounded-xl border border-outline-variant bg-surface-container-lowest p-md transition-shadow hover:shadow-md">
                <div className="mb-sm flex items-start justify-between gap-sm">
                  <h3 className="font-bold text-lg text-on-surface">{item.title}</h3>
                  <span className="whitespace-nowrap rounded-full border border-outline-variant bg-surface-container-low px-xs py-[2px] text-on-surface-variant text-xs">
                    {item.status}
                  </span>
                </div>
                <div className="mt-auto flex items-center gap-sm border-outline-variant border-t pt-sm">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary-container font-bold text-on-secondary-container text-sm">
                    {avatarInitial(item.responderName)}
                  </div>
                  <div className="flex flex-col">
                    <span className="text-on-surface-variant text-xs">回答者</span>
                    <span className="text-on-surface text-sm">{item.responderName}</span>
                  </div>
                </div>
              </article>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
