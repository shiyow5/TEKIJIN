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
 * Linking is "Sign in with Slack": clicking navigates the WHOLE page to
 * Slack's authorize URL (an external OAuth flow, not a fetch) and back to
 * `/chat?slack=linked|error` once `GET /slack/oauth/callback` finishes.
 */

import { useAuth } from "@/components/AuthProvider";
import { ApiError, getSlackAuthorizeUrl, getSlackStatus, postSlackUnlink } from "@/lib/api-client";
import { useEffect, useState } from "react";

type Status = "loading" | "unavailable" | "linked" | "unlinked";

export function SlackLinkButton() {
  const { principal } = useAuth();
  const [status, setStatus] = useState<Status>("loading");
  const [busy, setBusy] = useState(false);
  const linkable = principal != null && !principal.is_admin;

  useEffect(() => {
    if (!linkable) return;
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
  }, [linkable]);

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
    <button
      type="button"
      onClick={handleConnect}
      disabled={busy}
      className="inline-flex items-center gap-xs rounded-full border border-outline-variant bg-surface-container-lowest px-sm py-1 font-bold text-on-surface text-xs shadow-sm transition-colors hover:bg-surface-container-low disabled:opacity-50"
    >
      Slackと連携
    </button>
  );
}
