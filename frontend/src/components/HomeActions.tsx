"use client";

/**
 * Home hub action cards (#124), split out from `app/page.tsx` so the
 * dashboard card can be gated on the signed-in principal's role (#347): the
 * admin nav already hides `/dashboard` for non-admins (`AppHeader.tsx`), but
 * the hub's action cards had no such check, so a regular employee could land
 * on a 403 with a misleading "try again later" message. The rest of the
 * landing page stays a plain server component; only this role-dependent
 * slice needs client state.
 */

import { useAuth } from "@/components/AuthProvider";
import Link from "next/link";
import type { ReactNode } from "react";

function IconHistory() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      className="h-6 w-6"
    >
      <path d="M3 12a9 9 0 1 0 3-6.7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 4v4h4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M12 8v4l3 3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconInbox() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      className="h-6 w-6"
    >
      <path d="M4 13l2.5-7h11L20 13v5H4v-5z" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 13h4l1.5 2.5h5L16 13h4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconChart() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      className="h-6 w-6"
    >
      <path d="M4 20V4M4 20h16" strokeLinecap="round" />
      <path d="M8 20v-6M12 20V8M16 20v-9" strokeLinecap="round" />
    </svg>
  );
}

function IconArrow() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
      className="h-5 w-5"
    >
      <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

interface Action {
  href: string;
  label: string;
  description: string;
  icon: ReactNode;
  accent: string;
  /** Hidden unless the signed-in principal is an admin (#347). */
  adminOnly?: boolean;
}

const ACTIONS: Action[] = [
  {
    // "質問する" moved into the hero's own question bar (#392) — a link here
    // would just duplicate it. 質問履歴 fills the slot instead.
    href: "/history",
    label: "質問履歴",
    description: "過去に自分が聞いた質問と、その後のやり取りを振り返れます。",
    icon: <IconHistory />,
    accent: "bg-primary-container text-on-primary-container",
  },
  {
    href: "/inbox",
    label: "回答する",
    description: "自分に届いた質問を受信箱で確認して答えます。",
    icon: <IconInbox />,
    accent: "bg-primary-container text-on-primary-container",
  },
  {
    href: "/dashboard",
    label: "ダッシュボード",
    description: "自己解決率や負荷分散など、活動状況の集計を見ます。",
    icon: <IconChart />,
    accent: "bg-tertiary-container text-on-tertiary-container",
    adminOnly: true,
  },
];

export function HomeActions() {
  const { principal } = useAuth();
  const actions = ACTIONS.filter((action) => !action.adminOnly || principal?.is_admin);
  // Admin sees all 3 cards (3-col grid); a regular user only sees 2 (#347), so a
  // fixed 3-col grid would leave the third column's width as dead space on the
  // right instead of the remaining cards filling the row (#368).
  const gridColsClass = actions.length >= 3 ? "sm:grid-cols-3" : "sm:grid-cols-2";

  return (
    <ul className={`grid grid-cols-1 gap-md ${gridColsClass}`}>
      {actions.map((action) => (
        <li key={action.href}>
          <Link
            href={action.href}
            className="group flex h-full flex-col gap-sm rounded-xl border border-outline-variant bg-surface-container-lowest p-md shadow-sm transition-colors hover:bg-surface-container-low"
          >
            <span
              className={`flex h-11 w-11 items-center justify-center rounded-full ${action.accent}`}
            >
              {action.icon}
            </span>
            <span className="flex items-center gap-xs font-bold text-lg text-on-surface">
              {action.label}
              <span className="text-on-surface-variant transition-transform group-hover:translate-x-1">
                <IconArrow />
              </span>
            </span>
            <span className="text-on-surface-variant text-sm leading-relaxed">
              {action.description}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
