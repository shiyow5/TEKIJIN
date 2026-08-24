import { ChatScreen } from "@/components/ChatScreen";

/**
 * Chat route (#224): the accepted-recommendation thread list + history for the
 * acting user. `?thread=<recommendation_id>` deep-links a specific thread open
 * (used by `AnswerScreen`'s "チャットを開く" CTA after accepting).
 */
export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ thread?: string }>;
}) {
  const { thread } = await searchParams;
  return <ChatScreen initialThreadId={thread} />;
}
