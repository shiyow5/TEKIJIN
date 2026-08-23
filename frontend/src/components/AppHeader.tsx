"use client";

/**
 * Application header: product identity plus the current-user switcher.
 *
 * The prototype has no auth, so the switcher chooses the acting employee from
 * the directory (`GET /employees`, via {@link useCurrentUser}). Picking a user
 * updates the shared context, so the asker screen's `asker_id` and the responder
 * inbox follow the selection. While the directory loads (or if it fails), the
 * switcher shows a disabled placeholder rather than an empty control.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";

export function AppHeader() {
  const { employees, currentUserId, setCurrentUserId, loading } = useCurrentUser();
  const ready = employees.length > 0 && currentUserId !== null;

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
          className="rounded-md border border-outline bg-surface-container-lowest px-sm py-xs text-sm disabled:text-on-surface-variant"
          value={ready ? currentUserId : ""}
          disabled={!ready}
          onChange={(e) => setCurrentUserId(e.target.value)}
        >
          {ready ? (
            employees.map((employee) => (
              <option key={employee.id} value={employee.id}>
                {employee.dept ? `${employee.name}（${employee.dept}）` : employee.name}
              </option>
            ))
          ) : (
            <option value="">{loading ? "読み込み中…" : "利用できません"}</option>
          )}
        </select>
      </label>
    </header>
  );
}
