import { ChatScreen } from "@/components/ChatScreen";

/**
 * Chat route (#224): the accepted-recommendation thread list + history for the
 * acting user. `?thread=<recommendation_id>` deep-links a specific thread open
 * (used by `AnswerScreen`'s "チャットを開く" CTA after accepting).
 * `?slack=linked|error` is the Slack OAuth callback's redirect result
 * (`GET /slack/oauth/callback`, #slack-integration).
 */
export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ thread?: string; slack?: string }>;
}) {
  const { thread, slack } = await searchParams;
  const initialSlackResult = slack === "linked" || slack === "error" ? slack : undefined;
  return <ChatScreen initialThreadId={thread} initialSlackResult={initialSlackResult} />;
}
