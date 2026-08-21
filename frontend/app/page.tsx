import Link from "next/link";

/**
 * Landing placeholder for the foundation milestone.
 * Links point at routes that later screen work (#35-39) will implement.
 */
const SCREENS = [
  { href: "/questions", label: "質問する", description: "困りごとを投稿する" },
  { href: "/results", label: "マッチング結果", description: "回答できる人を探す" },
  { href: "/answers", label: "回答する", description: "届いた質問に答える" },
  { href: "/dashboard", label: "ダッシュボード", description: "活動状況を確認する" },
] as const;

export default function HomePage() {
  return (
    <section className="flex flex-col gap-lg">
      <div className="flex flex-col gap-sm">
        <h1 className="font-bold text-2xl text-on-surface">TEKIJIN（たずねーる）</h1>
        <p className="max-w-2xl text-on-surface-variant">
          社内の「訊きづらさ」を溶かす、質問と回答のマッチング支援ツール。
          以下は各画面への導線プレースホルダです（画面実装は後続タスクで追加します）。
        </p>
      </div>

      <ul className="grid grid-cols-1 gap-md sm:grid-cols-2">
        {SCREENS.map((screen) => (
          <li key={screen.href}>
            <Link
              href={screen.href}
              className="flex flex-col gap-xs rounded-lg border border-outline-variant bg-surface-container-lowest p-md transition-colors hover:bg-surface-container-low"
            >
              <span className="font-bold text-lg text-primary">{screen.label}</span>
              <span className="text-on-surface-variant text-sm">{screen.description}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
