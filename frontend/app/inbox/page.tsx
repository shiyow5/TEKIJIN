import { InboxScreen } from "@/components/InboxScreen";

/**
 * Inbox route (#123, the responder's entry point). Thin client-screen wrapper:
 * the `InboxScreen` component loads GET /inbox for the current user and links
 * each pending handoff to `/answer/{session_id}`.
 */
export default function InboxPage() {
  return <InboxScreen />;
}
