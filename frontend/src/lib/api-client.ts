/**
 * Fetch-based client for the TEKIJIN API boundary.
 *
 * `postAsk` (POST /ask) starts a question; `postAnswer` (POST /answer) resumes a
 * paused run with a clarification reply or a responder outcome. The base URL
 * comes from config (`NEXT_PUBLIC_API_BASE_URL`) — never hardcoded at call
 * sites. Non-2xx responses throw a typed {@link ApiError}.
 */

import type {
  AckResponse,
  AskRequest,
  DashboardResponse,
  DeclineNotification,
  DocumentDetail,
  EmployeeListResponse,
  EmployeeSummary,
  HandoffDraftRequest,
  HandoffResponse,
  HandoffSelectRequest,
  HandoffSelectResponse,
  InboxItem,
  InboxResponse,
  MessageCreateRequest,
  MessageItem,
  MessagesResponse,
  NotificationAckRequest,
  NotificationAckResponse,
  NotificationsResponse,
  RecentQuestionItem,
  RecentQuestionsResponse,
  ResumeRequest,
} from "@/lib/api-types";
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

/** DELETE `{base}{path}`, throwing {@link ApiError} on non-2xx. No response body expected (204). */
async function deleteRequest(path: string, options: RequestOptions = {}): Promise<void> {
  const baseUrl = (options.baseUrl ?? getApiBaseUrl()).replace(/\/+$/, "");
  const doFetch = options.fetchImpl ?? fetch;

  const response = await doFetch(`${baseUrl}${path}`, {
    method: "DELETE",
    signal: options.signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
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
 * POST /handoff/draft — persist the asker's edited hand-off draft so the
 * responder sees the edited text (#174). Draft-only. Throws {@link ApiError}
 * with 404 (no hand-off pending / already answered) or 409 (awaiting a
 * clarification instead).
 */
export function updateHandoffDraft(
  request: HandoffDraftRequest,
  options: RequestOptions = {},
): Promise<AckResponse> {
  return postJson<AckResponse>("/handoff/draft", request, options);
}

/**
 * POST /handoff/select — the asker picks a different (of the currently shown)
 * candidate as the hand-off target; the draft is regenerated for them
 * (#200/#A1/#204). Throws {@link ApiError} with 404 (no hand-off pending /
 * already answered), 409 (awaiting a clarification instead), or 422
 * (`person_id` is not among the currently shown recommendations).
 */
export function selectHandoffCandidate(
  request: HandoffSelectRequest,
  options: RequestOptions = {},
): Promise<HandoffSelectResponse> {
  return postJson<HandoffSelectResponse>("/handoff/select", request, options);
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

/**
 * GET /dashboard — aggregate usage metrics for the admin dashboard (画面5).
 * Aggregate-only: no individual question content is returned.
 */
export function getDashboard(options: RequestOptions = {}): Promise<DashboardResponse> {
  return getJson<DashboardResponse>("/dashboard", options);
}

/**
 * GET /employees — the employee directory for the current-user switcher. The
 * prototype has no auth, so the acting user is chosen from this list. Returns the
 * unwrapped array (ids in the external "E###" form).
 */
export async function getEmployees(options: RequestOptions = {}): Promise<EmployeeSummary[]> {
  const body = await getJson<EmployeeListResponse>("/employees", options);
  return body.employees;
}

/**
 * GET /inbox — the questions currently awaiting `responderId` (the responder
 * inbox, #123). `responderId` is the external "E###" form. Returns the unwrapped
 * items array, newest first; each carries a `session_id` for `/answer/{id}`.
 */
export async function getInbox(
  responderId: string,
  options: RequestOptions = {},
): Promise<InboxItem[]> {
  const query = `?responder_id=${encodeURIComponent(responderId)}`;
  const body = await getJson<InboxResponse>(`/inbox${query}`, options);
  return body.items;
}

export interface GetRecentQuestionsOptions extends RequestOptions {
  /** Cap on returned items; omitted uses the backend default (5, "最近のあなたの質問"). The full-history view (#208/#F9) passes a larger value. */
  limit?: number;
}

/**
 * GET /questions — the asker's own recent questions with resolution state
 * (画面1 の "最近のあなたの質問"). `askerId` is the external "E###" form.
 * Returns the unwrapped items array, newest first. Soft-deleted questions
 * (#207/#F8) are excluded by the backend.
 */
export async function getRecentQuestions(
  askerId: string,
  options: GetRecentQuestionsOptions = {},
): Promise<RecentQuestionItem[]> {
  const { limit, ...requestOptions } = options;
  const params = new URLSearchParams({ asker_id: askerId });
  if (limit !== undefined) params.set("limit", String(limit));
  const body = await getJson<RecentQuestionsResponse>(
    `/questions?${params.toString()}`,
    requestOptions,
  );
  return body.items;
}

/**
 * DELETE /questions/{id} — soft-delete one of the asker's own past questions
 * (#207/#F8). `askerId` is the external "E###" form. Throws {@link ApiError}
 * with 404 (not found / not owned / already deleted) or 409 (a responder is
 * currently being asked — a live pending hand-off blocks deletion).
 */
export function deleteQuestion(
  questionId: string,
  askerId: string,
  options: RequestOptions = {},
): Promise<void> {
  const query = `?asker_id=${encodeURIComponent(askerId)}`;
  return deleteRequest(`/questions/${encodeURIComponent(questionId)}${query}`, options);
}

/**
 * GET /notifications — decline events the asker hasn't seen yet, newest first
 * (#E7). `askerId` is the external "E###" form. Returns the unwrapped items array.
 */
export async function getNotifications(
  askerId: string,
  options: RequestOptions = {},
): Promise<DeclineNotification[]> {
  const query = `?asker_id=${encodeURIComponent(askerId)}`;
  const body = await getJson<NotificationsResponse>(`/notifications${query}`, options);
  return body.items;
}

/** POST /notifications/ack — mark decline notifications as seen (#E7). */
export function ackNotifications(
  request: NotificationAckRequest,
  options: RequestOptions = {},
): Promise<NotificationAckResponse> {
  return postJson<NotificationAckResponse>("/notifications/ack", request, options);
}

/**
 * GET /messages — a session's post-acceptance chat thread, oldest first (#E6).
 * Empty (not an error) before the thread has any messages. Returns the
 * unwrapped items array.
 */
export async function getMessages(
  sessionId: string,
  options: RequestOptions = {},
): Promise<MessageItem[]> {
  const query = `?session_id=${encodeURIComponent(sessionId)}`;
  const body = await getJson<MessagesResponse>(`/messages${query}`, options);
  return body.items;
}

/**
 * POST /messages — post one chat message on a session's post-acceptance thread
 * (#E6). Throws {@link ApiError} with 404 (unknown session), 409 (not yet
 * accepted — the thread is not open), or 403 (sender is not a participant).
 */
export function postMessage(
  request: MessageCreateRequest,
  options: RequestOptions = {},
): Promise<MessageItem> {
  return postJson<MessageItem>("/messages", request, options);
}

/** Fetch one internal document's full content for the viewer (GET /documents/{id}, #143). */
export async function getDocument(
  docId: string,
  options: RequestOptions = {},
): Promise<DocumentDetail> {
  return getJson<DocumentDetail>(`/documents/${encodeURIComponent(docId)}`, options);
}

/**
 * Drive a paused run forward by consuming one GET /events pass to completion.
 *
 * POST /answer only *queues* the resume; the backend advances the graph when an
 * /events reader enters its dispatch loop. The responder's answer screen has no
 * long-lived stream of its own, so after submitting an outcome it calls this to
 * guarantee the queued resume is consumed — accept reaches C8 (done), decline
 * reroutes to the next candidate — instead of depending on the asker keeping a
 * tab open. The streamed content is irrelevant here; we only read it to
 * completion (the stream ends when the run next interrupts or terminates). This
 * is best-effort: a non-2xx (e.g. the run already finished → 404) or a transport
 * error is swallowed, since the outcome is already recorded server-side.
 */
export async function advanceSession(
  sessionId: string,
  options: RequestOptions = {},
): Promise<void> {
  const baseUrl = (options.baseUrl ?? getApiBaseUrl()).replace(/\/+$/, "");
  const doFetch = options.fetchImpl ?? fetch;
  try {
    const response = await doFetch(`${baseUrl}/events/${encodeURIComponent(sessionId)}`, {
      method: "GET",
      signal: options.signal,
    });
    await response.text();
  } catch {
    // Best-effort: the outcome is already persisted; the asker's own reconnecting
    // stream is the backup consumer. Never surface this to the responder.
  }
}
