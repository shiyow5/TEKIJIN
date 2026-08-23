import { DocumentViewer } from "@/components/DocumentViewer";

/**
 * Document viewer route (#143). Thin server wrapper: it resolves the document id
 * from the path and hands it to the client `DocumentViewer`, which loads
 * GET /documents/{id} and renders the full internal document.
 */
export default async function DocumentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <DocumentViewer docId={id} />;
}
