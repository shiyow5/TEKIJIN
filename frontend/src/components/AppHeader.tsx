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
 *
 * Switching also navigates home (#210). The header is in the root layout, so the
 * switcher is reachable from every screen — including ones that never read
 * ``currentUserId`` (``/answer/[session_id]``) and would otherwise sit there
 * unchanged. Becoming a different person mid-flow makes the previous user's
 * screen (their inbox, their session) meaningless, so we start over at the hub;
 * anything unsent on that screen is deliberately dropped with it.
 */

import { useCurrentUser } from "@/components/CurrentUserProvider";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const NAV = [
  { href: "/questions", label: "質問する" },
  { href: "/inbox", label: "受信箱" },
  { href: "/dashboard", label: "ダッシュボード" },
] as const;

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppHeader() {
  const { employees, currentUserId, setCurrentUserId, loading, error, reload } = useCurrentUser();
  const ready = employees.length > 0 && currentUserId !== null;
  const pathname = usePathname() ?? "";
  const router = useRouter();

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

      <label
        className="flex items-center gap-xs text-on-surface-variant text-xs"
        aria-busy={loading}
        title="プロトタイプ用の擬似ログイン（認証なし）。動作確認のため利用者を切り替えます。"
      >
        <span className="rounded bg-surface-container-high px-xs py-[1px] text-on-surface-variant">
          デモ用
        </span>
        <span>利用者を切替</span>
        <select
          aria-label="利用者を切替（デモ用の擬似ログイン）"
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
    </header>
  );
}
