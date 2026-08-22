/**
 * Fetch-based client for the TEKIJIN API boundary.
 *
 * `postAsk` (POST /ask) starts a question; `postAnswer` (POST /answer) resumes a
 * paused run with a clarification reply or a responder outcome. The base URL
 * comes from config (`NEXT_PUBLIC_API_BASE_URL`) — never hardcoded at call
 * sites. Non-2xx responses throw a typed {@link ApiError}.
 */

import type { AckResponse, AskRequest, HandoffResponse, ResumeRequest } from "@/lib/api-types";
import { getApiBaseUrl } from "@/lib/config";

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

/** POST `body` as JSON to `{base}{path}`, throwing {@link ApiError} on non-2xx. */
async function postJson<T>(path: string, body: unknown, options: RequestOptions = {}): Promise<T> {
  // Trim a trailing slash on an explicit override so `${base}${path}` never
  // doubles up; the config default is already normalized.
  const baseUrl = (options.baseUrl ?? getApiBaseUrl()).replace(/\/+$/, "");
  const doFetch = options.fetchImpl ?? fetch;

  const response = await doFetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }

  return (await response.json()) as T;
}

/** GET `{base}{path}` as JSON, throwing {@link ApiError} on non-2xx. */
async function getJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const baseUrl = (options.baseUrl ?? getApiBaseUrl()).replace(/\/+$/, "");
  const doFetch = options.fetchImpl ?? fetch;

  const response = await doFetch(`${baseUrl}${path}`, {
    method: "GET",
    signal: options.signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }

  return (await response.json()) as T;
}

/**
 * POST /ask — start a question for a session. Returns the acknowledgement; the
 * actual stream flows over GET /events (subscribed via `useEventStream`).
 */
export function postAsk(request: AskRequest, options: RequestOptions = {}): Promise<AckResponse> {
  return postJson<AckResponse>("/ask", request, options);
}

/**
 * POST /answer — resume a paused run. Carries either a clarification `reply`
 * (answering a `followup` interrupt) or a responder `outcome`. The stream
 * resumes over the still-open GET /events connection.
 */
export function postAnswer(
  request: ResumeRequest,
  options: RequestOptions = {},
): Promise<AckResponse> {
  return postJson<AckResponse>("/answer", request, options);
}

/**
 * GET /handoff/{session_id} — the responder-facing view of a session paused at
 * the `send` interrupt (product-spec 画面4). Read-only: it does not advance the
 * graph. Throws {@link ApiError} with status 404 (no handoff pending) or 409
 * (the session is awaiting a clarification instead).
 */
export function getHandoff(
  sessionId: string,
  options: RequestOptions = {},
): Promise<HandoffResponse> {
  return getJson<HandoffResponse>(`/handoff/${encodeURIComponent(sessionId)}`, options);
}
