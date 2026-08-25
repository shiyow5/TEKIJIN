import Link from "next/link";

export interface PageBackLinkProps {
  href: string;
  label: string;
  className?: string;
}

/**
 * A consistent, explicit way to move one level up in the app.
 *
 * The destination is deliberately fixed instead of using browser history: a
 * deep link, reload, or visit from another site should still take the user to a
 * safe and understandable place inside TEKIJIN.
 */
export function PageBackLink({ href, label, className = "" }: PageBackLinkProps) {
  return (
    <Link
      href={href}
      className={`inline-flex min-h-[44px] items-center gap-xs self-start rounded-lg px-sm py-xs font-medium text-on-surface-variant text-sm transition-colors hover:bg-surface-container-low hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${className}`}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden="true"
        className="h-5 w-5 shrink-0"
      >
        <path d="M19 12H5M11 18l-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span>{label}</span>
    </Link>
  );
}
