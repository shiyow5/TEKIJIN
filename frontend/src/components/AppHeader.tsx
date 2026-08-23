"use client";

/**
 * Application header: product identity, global navigation, and the current-user
 * switcher.
 *
 * Navigation (#122) makes every main screen reachable in one click from anywhere
 * — previously there was none, so pages like the dashboard were dead-ends and the
 * responder inbox was undiscoverable. The brand links home; the active link is
 * marked with ``aria-current``.
 *
 * The switcher (no auth in the prototype) chooses the acting employee from the
 * directory (``GET /employees`` via {@link useCurrentUser}); the asker screen's
 * ``asker_id`` and the inbox follow the selection. While the directory loads (or
 * if it fails) the switcher shows a disabled placeholder.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/questions", label: "質問する" },
  { href: "/inbox", label: "受信箱" },
  { href: "/dashboard", label: "ダッシュボード" },
] as const;

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppHeader() {
  const { employees, currentUserId, setCurrentUserId, loading } = useCurrentUser();
  const ready = employees.length > 0 && currentUserId !== null;
  const pathname = usePathname() ?? "";

  return (
    <header className="flex flex-wrap items-center justify-between gap-sm border-outline-variant border-b bg-surface-container-lowest px-margin py-sm">
      <div className="flex flex-wrap items-center gap-md">
        <Link href="/" className="flex items-baseline gap-sm">
          <span className="font-bold text-on-surface text-xl">TEKIJIN</span>
          <span className="text-on-surface-variant text-sm">たずねーる</span>
        </Link>
        <nav aria-label="メインナビゲーション">
          <ul className="flex items-center gap-xs">
            {NAV.map((item) => {
              const active = isActive(pathname, item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={
                      active
                        ? "rounded-md bg-secondary-container px-sm py-xs font-bold text-on-secondary-container text-sm"
                        : "rounded-md px-sm py-xs text-on-surface-variant text-sm transition-colors hover:bg-surface-container-low"
                    }
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
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
