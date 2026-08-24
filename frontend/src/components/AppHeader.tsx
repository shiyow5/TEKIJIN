"use client";

/**
 * Application header: product identity, global navigation, the current user, and
 * (admin only) the demo user switcher.
 *
 * Navigation (#122) makes every main screen reachable in one click. The dashboard
 * link is ADMIN-ONLY (#241) — it aggregates everyone's activity. The brand links
 * home; the active link is marked with ``aria-current``.
 *
 * Auth (#241): a regular user sees their own name and a logout button. The ADMIN
 * additionally gets the demo switcher — choosing the acting employee from the
 * directory (``GET /employees`` via {@link useCurrentUser}); the asker screen's
 * ``asker_id`` and the inbox follow the selection. Switching also navigates home
 * (#210): becoming a different person mid-flow makes the previous screen
 * meaningless, so we start over at the hub.
 */

import { useAuth } from "@/components/AuthProvider";
import { useCurrentUser } from "@/components/CurrentUserProvider";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const NAV = [
  { href: "/questions", label: "質問する", adminOnly: false },
  { href: "/inbox", label: "受信箱", adminOnly: false },
  { href: "/dashboard", label: "ダッシュボード", adminOnly: true },
] as const;

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppHeader() {
  const { employees, currentUserId, setCurrentUserId, loading, error, reload, canSwitch } =
    useCurrentUser();
  const { principal, logout } = useAuth();
  const ready = employees.length > 0 && currentUserId !== null;
  const pathname = usePathname() ?? "";
  const router = useRouter();

  const handleLogout = async () => {
    await logout();
    router.replace("/login");
  };

  const nav = NAV.filter((item) => !item.adminOnly || principal?.is_admin);

  return (
    <header className="flex flex-wrap items-center justify-between gap-sm border-outline-variant border-b bg-surface-container-lowest px-margin py-sm">
      <div className="flex flex-wrap items-center gap-md">
        <Link href="/" aria-label="TEKIJIN ホーム">
          {/* Transparent-background logo from Next's /public (aspect ≈ 2.8:1).
              alt carries the brand name so the link's accessible name stays
              "TEKIJIN". */}
          <img src="/tekijin-logo.png" alt="TEKIJIN" className="h-10 w-auto" />
        </Link>
        <nav aria-label="メインナビゲーション">
          <ul className="flex items-center gap-xs">
            {nav.map((item) => {
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

      <div className="flex flex-wrap items-center gap-sm">
        {canSwitch ? (
          <label
            className="flex items-center gap-xs text-on-surface-variant text-xs"
            aria-busy={loading}
            title="管理者のデモ機能。動作確認のため、任意の利用者になりかわって表示します。"
          >
            <span className="rounded bg-surface-container-high px-xs py-[1px] text-on-surface-variant">
              デモ用
            </span>
            <span>利用者を切替</span>
            <select
              aria-label="利用者を切替（管理者デモ機能）"
              className="rounded-md border border-outline bg-surface-container-lowest px-sm py-xs text-sm disabled:text-on-surface-variant"
              value={ready ? currentUserId : ""}
              disabled={!ready}
              onChange={(e) => {
                setCurrentUserId(e.target.value);
                router.push("/");
              }}
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
            {error ? (
              <button
                type="button"
                onClick={reload}
                className="rounded-md border border-outline px-sm py-xs text-primary text-xs transition-colors hover:bg-surface-container-low"
              >
                利用者一覧の取得に失敗しました。再試行
              </button>
            ) : null}
          </label>
        ) : (
          <span className="text-on-surface-variant text-sm">
            {principal?.name ?? ""}
            {principal?.is_admin ? "（管理者）" : ""}
          </span>
        )}
        {principal ? (
          <button
            type="button"
            onClick={handleLogout}
            className="rounded-md border border-outline px-sm py-xs text-on-surface-variant text-sm transition-colors hover:bg-surface-container-low"
          >
            ログアウト
          </button>
        ) : null}
      </div>
    </header>
  );
}
