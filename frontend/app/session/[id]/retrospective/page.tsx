import { RetrospectiveScreen } from "@/components/RetrospectiveScreen";

/**
 * 直接相談のふりかえり route (#247). Thin wrapper: it only resolves the session id
 * from the path and hands it to the client `RetrospectiveScreen`, which loads
 * GET /handoff/{id} and renders the form.
 */
export default async function SessionRetrospectivePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <RetrospectiveScreen sessionId={id} />;
}
