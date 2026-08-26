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
import { NotificationBell } from "@/components/NotificationBell";
import type { EmployeeSummary } from "@/lib/api-types";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

const NAV = [
  { href: "/questions", label: "質問する", adminOnly: false },
  { href: "/history", label: "質問履歴", adminOnly: false },
  { href: "/inbox", label: "受信箱", adminOnly: false },
  // Chat is per-person (only your own accepted threads), so unlike the dashboard
  // it is NOT admin-only (#224).
  { href: "/chat", label: "チャット", adminOnly: false },
  // Company-wide (not scoped to the acting user), but still NOT admin-only:
  // the point is every user can discover someone else's past answer (#293, #301).
  { href: "/knowledge", label: "ナレッジ", adminOnly: false },
  { href: "/dashboard", label: "ダッシュボード", adminOnly: true },
] as const;

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function IconMenu() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      className="h-6 w-6"
    >
      <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
    </svg>
  );
}

function IconClose() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      className="h-6 w-6"
    >
      <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
    </svg>
  );
}

function NavLinks({
  items,
  pathname,
  onNavigate,
  className,
}: {
  items: readonly { href: string; label: string }[];
  pathname: string;
  onNavigate?: () => void;
  className: string;
}) {
  return (
    <ul className={className}>
      {items.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              onClick={onNavigate}
              className={
                active
                  ? "block rounded-md bg-secondary-container px-sm py-xs font-bold text-on-secondary-container text-sm"
                  : "block rounded-md px-sm py-xs text-on-surface-variant text-sm transition-colors hover:bg-surface-container-low"
              }
            >
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

// The admin-only demo switcher (badge + label + select + retry button),
// factored out so it can be rendered inline in the header row at every
// breakpoint while logout moves into the hamburger menu on narrow screens
// (#288) — the row itself needs `flex-wrap` (already on its parent) to absorb
// this cluster gracefully instead of overflowing.
function UserSwitcher({
  loading,
  error,
  reload,
  ready,
  currentUserId,
  employees,
  onChange,
  className,
}: {
  loading: boolean;
  error: boolean;
  reload: () => void;
  ready: boolean;
  currentUserId: string | null;
  employees: EmployeeSummary[];
  onChange: (id: string) => void;
  className: string;
}) {
  return (
    <label
      className={className}
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
        value={currentUserId ?? ""}
        disabled={!ready}
        onChange={(e) => onChange(e.target.value)}
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
  );
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
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  // Route changes (including the switcher's "go home") always close the mobile
  // menu — staying open after navigating away would sit there stale.
  // biome-ignore lint/correctness/useExhaustiveDependencies: re-run only when pathname changes.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    }

    function handlePointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || menuButtonRef.current?.contains(target)) return;
      setMenuOpen(false);
    }

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("mousedown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("mousedown", handlePointerDown);
    };
  }, [menuOpen]);

  return (
    // The white background spans the full viewport; the CONTENT is centred at
    // `max-w-content` by the inner wrapper. Constraining the <header> itself let
    // the body's tinted background show through beside it above 1440px (#250).
    <header className="border-outline-variant border-b bg-surface-container-lowest px-margin py-sm">
      <div className="mx-auto flex w-full max-w-content flex-wrap items-center justify-between gap-sm">
        <div className="flex flex-wrap items-center gap-md">
          <Link href="/" aria-label="TEKIJIN ホーム">
            {/* Transparent-background logo from Next's /public (aspect ≈ 2.8:1).
                alt carries the brand name so the link's accessible name stays
                "TEKIJIN". */}
            <img src="/tekijin-logo.png" alt="TEKIJIN" className="h-10 w-auto" />
          </Link>
          <nav aria-label="メインナビゲーション" className="hidden md:block">
            <NavLinks items={nav} pathname={pathname} className="flex items-center gap-xs" />
          </nav>
        </div>

        <div className="flex flex-wrap items-center gap-sm">
          {/* Decline notifications (#225). Logged out there is no acting user to
            poll for, so the bell stays out of the tree entirely. */}
          {principal ? <NotificationBell /> : null}
          {canSwitch ? (
            <UserSwitcher
              loading={loading}
              error={error}
              reload={reload}
              ready={ready}
              currentUserId={currentUserId}
              employees={employees}
              onChange={(id) => {
                setCurrentUserId(id);
                router.push("/");
              }}
              className="flex flex-wrap items-center gap-xs text-on-surface-variant text-xs"
            />
          ) : (
            <span className="text-on-surface-variant text-sm">
              {principal?.name ?? ""}
              {principal?.is_admin ? "（管理者）" : ""}
            </span>
          )}
          {/* Logout stays reachable inline on desktop; on narrow screens it
            moves into the hamburger menu so the row stays bell + switcher +
            menu toggle (#288). */}
          {principal ? (
            <button
              type="button"
              onClick={handleLogout}
              className="hidden rounded-md border border-outline px-sm py-xs text-on-surface-variant text-sm transition-colors hover:bg-surface-container-low md:block"
            >
              ログアウト
            </button>
          ) : null}

          <button
            ref={menuButtonRef}
            type="button"
            aria-expanded={menuOpen}
            aria-controls="mobile-nav-menu"
            aria-label={menuOpen ? "メニューを閉じる" : "メニューを開く"}
            onClick={() => setMenuOpen((open) => !open)}
            className="flex h-9 w-9 items-center justify-center rounded-md text-on-surface-variant transition-colors hover:bg-surface-container-low md:hidden"
          >
            {menuOpen ? <IconClose /> : <IconMenu />}
          </button>
        </div>
      </div>

      {menuOpen ? (
        <div
          id="mobile-nav-menu"
          ref={menuRef}
          className="mx-auto mt-sm w-full max-w-content border-outline-variant border-t pt-sm md:hidden"
        >
          <NavLinks
            items={nav}
            pathname={pathname}
            onNavigate={() => setMenuOpen(false)}
            className="flex flex-col gap-xs"
          />
          {principal ? (
            <div className="mt-sm border-outline-variant border-t pt-sm">
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-md border border-outline px-sm py-xs text-on-surface-variant text-sm transition-colors hover:bg-surface-container-low"
              >
                ログアウト
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}
