"use client";

/**
 * Slack account-link control, shown in the chat screen's top-right corner
 * (#slack-integration). Once BOTH parties of a "chat" hand-off have linked,
 * accepting the hand-off creates a shared private Slack channel for the two
 * of them (#hand-off-chat) — this button is just the identity-linking step;
 * the per-thread "open in Slack" link lives in the conversation pane itself.
 * The admin account is not a real employee and never receives chat messages,
 * so it renders nothing (mirrors `HomeActions`'s `adminOnly` gating).
 *
 * Linking is "Sign in with Slack": clicking navigates the WHOLE page to Slack's
 * authorize URL (an external OAuth flow, not a fetch). The callback cannot
 * finish the link on its own — it has no session, so it does not know who is
 * linking — so it returns to `/chat#slack_pending=<token>` and this component
 * redeems that token with the bearer token it already holds (#494). That is what
 * makes a link URL harmless to forward: whoever redeems it is who gets linked.
 */

import { useAuth } from "@/components/AuthProvider";
import {
  ApiError,
  completeSlackLink,
  getSlackAuthorizeUrl,
  getSlackStatus,
  postSlackUnlink,
} from "@/lib/api-client";
import { useEffect, useState } from "react";

type Status = "loading" | "unavailable" | "linked" | "unlinked";

export function SlackLinkButton() {
  const { principal } = useAuth();
  const [status, setStatus] = useState<Status>("loading");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const linkable = principal != null && !principal.is_admin;
  // Read once, on first render: the effect below strips it from the URL, so
  // re-reading `location.hash` later would see nothing.
  const [pendingToken] = useState<string | null>(() =>
    typeof window === "undefined"
      ? null
      : new URLSearchParams((window.location.hash ?? "").replace(/^#/, "")).get("slack_pending"),
  );

  // Redeem a pending link left in the fragment by the OAuth callback (#494).
  useEffect(() => {
    if (!linkable || pendingToken === null) return;
    // Clear it first: it is a one-shot credential and stays in history otherwise.
    window.history.replaceState(
      {},
      "",
      (window.location.pathname ?? "/") + (window.location.search ?? ""),
    );
    let active = true;
    completeSlackLink(pendingToken)
      .then(() => {
        if (active) setStatus("linked");
      })
      .catch((err) => {
        if (!active) return;
        setStatus("unlinked");
        const status = err instanceof ApiError ? err.status : 0;
        setError(
          // 403 means the link was STARTED by someone else — i.e. this URL was
          // forwarded. Say so plainly; "failed" would send them round again.
          status === 403
            ? "この連携はあなたが開始したものではありません。ご自身で「Slackと連携」からやり直してください。"
            : status === 409
              ? "このSlackアカウントは既に他の社員と連携されています。"
              : "Slack連携を完了できませんでした。もう一度お試しください。",
        );
      });
    return () => {
      active = false;
    };
  }, [linkable, pendingToken]);

  useEffect(() => {
    // Skipped while a pending link is being redeemed: `/slack/status` was
    // answered before the link existed, so letting it land would overwrite the
    // redemption result and show "not linked" right after linking.
    if (!linkable || pendingToken !== null) return;
    let active = true;
    getSlackStatus()
      .then((res) => {
        if (active) setStatus(res.linked ? "linked" : "unlinked");
      })
      .catch(() => {
        if (active) setStatus("unlinked");
      });
    return () => {
      active = false;
    };
  }, [linkable, pendingToken]);

  if (!linkable || status === "loading") return null;

  async function handleConnect() {
    setBusy(true);
    try {
      const { url } = await getSlackAuthorizeUrl();
      window.location.href = url;
    } catch (err) {
      setBusy(false);
      setStatus(err instanceof ApiError && err.status === 503 ? "unavailable" : "unlinked");
    }
  }

  async function handleUnlink() {
    setBusy(true);
    try {
      await postSlackUnlink();
      setStatus("unlinked");
    } catch {
      // Leave `status` as "linked" — the unlink didn't happen, so the UI
      // must keep reflecting that rather than silently doing nothing.
    } finally {
      setBusy(false);
    }
  }

  if (status === "unavailable") {
    return <span className="text-on-surface-variant text-xs">Slack連携は現在利用できません</span>;
  }

  if (status === "linked") {
    return (
      <div className="flex items-center gap-sm text-xs">
        <span className="rounded-full bg-secondary-container px-sm py-1 font-bold text-on-secondary-container">
          Slack連携済み
        </span>
        <button
          type="button"
          onClick={handleUnlink}
          disabled={busy}
          className="text-on-surface-variant underline transition-colors hover:text-on-surface disabled:opacity-50"
        >
          解除
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-xs">
      <button
        type="button"
        onClick={handleConnect}
        disabled={busy}
        className="inline-flex items-center gap-xs rounded-full border border-outline-variant bg-surface-container-lowest px-sm py-1 font-bold text-on-surface text-xs shadow-sm transition-colors hover:bg-surface-container-low disabled:opacity-50"
      >
        Slackと連携
      </button>
      {error ? (
        <p role="alert" className="max-w-xs text-right text-error text-xs">
          {error}
        </p>
      ) : null}
    </div>
  );
}
