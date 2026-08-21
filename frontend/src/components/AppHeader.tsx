/**
 * Application header: product identity plus a user-switch placeholder.
 *
 * The user switcher is a static placeholder for the foundation milestone;
 * real user/session wiring lands with later screen work (#35-39).
 */

const PLACEHOLDER_USERS = ["山田 太郎", "佐藤 花子", "鈴木 一郎"] as const;

export function AppHeader() {
  return (
    <header className="flex items-center justify-between gap-md border-outline-variant border-b bg-surface-container-lowest px-margin py-sm">
      <div className="flex items-baseline gap-sm">
        <span className="font-bold text-on-surface text-xl">TEKIJIN</span>
        <span className="text-on-surface-variant text-sm">たずねーる</span>
      </div>

      <label className="flex items-center gap-sm text-on-surface-variant text-sm">
        <span>ユーザー切替</span>
        <select
          aria-label="ユーザー切替"
          className="rounded-md border border-outline bg-surface-container-lowest px-sm py-xs text-on-surface text-sm"
          defaultValue={PLACEHOLDER_USERS[0]}
        >
          {PLACEHOLDER_USERS.map((user) => (
            <option key={user} value={user}>
              {user}
            </option>
          ))}
        </select>
      </label>
    </header>
  );
}
