/**
 * Fetch-based client for the TEKIJIN API boundary.
 *
 * #35 wires only `postAsk` (POST /ask). The base URL comes from config
 * (`NEXT_PUBLIC_API_BASE_URL`) — never hardcoded at call sites. Non-2xx
 * responses throw a typed {@link ApiError}.
 */

import type { AckResponse, AskRequest } from "@/lib/api-types";
import { getApiBaseUrl, normalizeBaseUrl } from "@/lib/config";

/** Error thrown for a non-2xx API response, carrying the HTTP status. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface RequestOptions {
  /** Override the resolved base URL (mainly for tests / SSR wiring). */
  baseUrl?: string;
  /** Override the `fetch` implementation (mainly for tests). */
  fetchImpl?: typeof fetch;
  /** Optional abort signal. */
  signal?: AbortSignal;
}

async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown; error?: unknown };
    const detail = body.detail ?? body.error;
    if (typeof detail === "string" && detail.trim() !== "") {
      return detail;
    }
  } catch {
    // Non-JSON or empty body — fall through to the status-based message.
  }
  return `Request failed with status ${response.status}`;
}

/**
 * POST /ask — start a question for a session. Returns the acknowledgement; the
 * actual stream flows over GET /events (subscribed in #36). Throws
 * {@link ApiError} on a non-2xx response.
 */
export async function postAsk(
  request: AskRequest,
  options: RequestOptions = {},
): Promise<AckResponse> {
  const baseUrl =
    options.baseUrl !== undefined ? normalizeBaseUrl(options.baseUrl) : getApiBaseUrl();
  const doFetch = options.fetchImpl ?? fetch;

  const response = await doFetch(`${baseUrl}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }

  return (await response.json()) as AckResponse;
}
