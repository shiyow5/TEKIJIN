import { SessionStreamProvider } from "@/components/SessionStreamProvider";
import type { ReactNode } from "react";

/**
 * Session layout — resolves the route id and mounts the client
 * `SessionStreamProvider` once for the whole `/session/[id]` subtree. The single
 * `useEventStream` subscription is shared by the processing screen and the
 * result screen, so accumulated recommend/route/draft survive navigation
 * between them. Thin server wrapper: it only awaits `params`.
 */
export default async function SessionLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SessionStreamProvider sessionId={id}>{children}</SessionStreamProvider>;
}
