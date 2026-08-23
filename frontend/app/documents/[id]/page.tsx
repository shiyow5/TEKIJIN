import { DocumentViewer } from "@/components/DocumentViewer";

/**
 * Document viewer route (#143). Thin server wrapper: it resolves the document id
 * from the path and hands it to the client `DocumentViewer`, which loads
 * GET /documents/{id} and renders the full internal document.
 *
 * `?from=<session_id>` (set by the linking result/processing screen) lets the
 * viewer send the reader back to the session they came from instead of the empty
 * question form (#179).
 */
export default async function DocumentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ from?: string }>;
}) {
  const { id } = await params;
  const { from } = await searchParams;
  return <DocumentViewer docId={id} fromSessionId={from} />;
}
