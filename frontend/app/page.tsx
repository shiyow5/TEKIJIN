import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Landing hub (#124). A real home, not a placeholder: a hero that states the
 * product's promise, a primary call-to-action, role-oriented action cards, and
 * a three-step "how it works" strip. Every link points at an existing route
 * (#121). Server component — no client state.
 *
 * The hero copy follows the #292 product direction: implicit knowledge is
 * accumulated and converted into explicit knowledge over time, so the answer
 * source is no longer framed as "always a person" (#324). It stays consistent
 * with the STEPS strip below by describing self-answer as a growing future
 * capability, not a live one — `self_answer_enabled` still defaults to off
 * (#291 part3), so the concrete flow today is still "AI forwards, a person
 * answers."
 */

function IconChat() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      className="h-6 w-6"
    >
      <path d="M4 5h16v11H8l-4 4V5z" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8 9h8M8 12h5" strokeLinecap="round" />
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
}

const ACTIONS: Action[] = [
  {
    href: "/questions",
    label: "質問する",
    description: "困りごとを書くと、答えられそうな人へAIが取り次ぎます。",
    icon: <IconChat />,
    accent: "bg-primary-container text-on-primary-container",
  },
  {
    href: "/inbox",
    label: "回答する",
    description: "自分に届いた質問を受信箱で確認して答えます。",
    icon: <IconInbox />,
    accent: "bg-secondary-container text-on-secondary-container",
  },
  {
    href: "/dashboard",
    label: "ダッシュボード",
    description: "自己解決率や負荷分散など、活動状況の集計を見ます。",
    icon: <IconChart />,
    accent: "bg-tertiary-container text-on-tertiary-container",
  },
];

const STEPS: { n: string; title: string; body: string }[] = [
  { n: "1", title: "質問を書く", body: "カテゴリ選択は不要。ふだんの言葉でそのまま。" },
  { n: "2", title: "AIが取り次ぐ", body: "社内の実績から、答えられそうな人を推薦。" },
  { n: "3", title: "人が答える", body: "AIが選んだ相手が、あなたに直接回答します。" },
];

export default function HomePage() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-lg py-lg">
      <section className="flex flex-col items-center gap-md rounded-xl border border-outline-variant bg-surface-container-low px-margin py-lg text-center">
        <h1 className="font-bold text-4xl text-on-surface tracking-tight">TEKIJIN</h1>
        <p className="max-w-2xl text-on-surface-variant leading-relaxed">
          社内の「訊きづらさ」を溶かす、質問と回答のマッチング支援ツール。
          AIが最適な相手を見つけて取り次ぎ、やり取りは会社の知識として少しずつ蓄積されていきます。
          貯まるほど、AIが自ら出典つきで答えられる場面も増えていきます。
        </p>
        <Link
          href="/questions"
          className="mt-sm inline-flex min-h-[48px] items-center gap-sm rounded-full bg-primary px-lg py-sm font-bold text-lg text-on-primary shadow-md transition-colors hover:bg-primary-container hover:text-on-primary-container"
        >
          質問する
          <IconArrow />
        </Link>
      </section>

      <ul className="grid grid-cols-1 gap-md sm:grid-cols-3">
        {ACTIONS.map((action) => (
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

      <section className="flex flex-col gap-md rounded-xl border border-outline-variant border-dashed bg-surface-container-lowest p-md">
        <h2 className="font-bold text-on-surface">使い方</h2>
        <ol className="grid grid-cols-1 gap-md sm:grid-cols-3">
          {STEPS.map((step) => (
            <li key={step.n} className="flex flex-col gap-xs">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary font-bold text-on-primary text-sm">
                {step.n}
              </span>
              <span className="font-bold text-on-surface text-sm">{step.title}</span>
              <span className="text-on-surface-variant text-sm leading-relaxed">{step.body}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
