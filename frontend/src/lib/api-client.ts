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
  DeleteQuestionResponse,
  DocumentDetail,
  EmployeeListResponse,
  EmployeeSummary,
  HandoffCorrectRequest,
  HandoffDraftRequest,
  HandoffExcludeRequest,
  HandoffRedraftRequest,
  HandoffResponse,
  HandoffSelectRequest,
  HandoffSelectResponse,
  InboxItem,
  InboxResponse,
  LoginRequest,
  LoginResponse,
  NotificationAckRequest,
  NotificationAckResponse,
  NotificationsResponse,
  Principal,
  RecentQuestionItem,
  RecentQuestionsResponse,
  ResolveQuestionResponse,
  ResumeRequest,
} from "@/lib/api-types";
import { getAuthToken } from "@/lib/auth-token";
import { getApiBaseUrl } from "@/lib/config";

/** Authorization header for the current token, or an empty object when logged out. */
function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

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
    headers: { "Content-Type": "application/json", ...authHeaders() },
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
    headers: { ...authHeaders() },
    signal: options.signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }

  return (await response.json()) as T;
}

/** DELETE `{base}{path}` as JSON, throwing {@link ApiError} on non-2xx. */
async function deleteJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const baseUrl = (options.baseUrl ?? getApiBaseUrl()).replace(/\/+$/, "");
  const doFetch = options.fetchImpl ?? fetch;

  const response = await doFetch(`${baseUrl}${path}`, {
    method: "DELETE",
    headers: { ...authHeaders() },
    signal: options.signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }

  return (await response.json()) as T;
}

/**
 * POST /auth/login — exchange email+password for a bearer token + the principal.
 * Throws {@link ApiError} with 401 (bad credentials) or 429 (rate limited).
 */
export function postLogin(
  request: LoginRequest,
  options: RequestOptions = {},
): Promise<LoginResponse> {
  return postJson<LoginResponse>("/auth/login", request, options);
}

/**
 * GET /auth/me — the principal for the current token (session restore). Throws
 * {@link ApiError} with 401 when the token is missing/expired.
 */
export function getMe(options: RequestOptions = {}): Promise<Principal> {
  return getJson<Principal>("/auth/me", options);
}

/**
 * POST /auth/logout — stateless server ack; the caller drops the local token.
 * Best-effort: a transport error is swallowed (logout still clears the token).
 */
export async function postLogout(options: RequestOptions = {}): Promise<void> {
  try {
    await postJson<AckResponse>("/auth/logout", {}, options);
  } catch {
    // The token is cleared client-side regardless; nothing to surface.
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
 * POST /handoff/exclude — the asker excludes the current send target
 * ("この人には聞かない"), rerouting to a freshly-scored next candidate (#260). The
 * new candidate + draft arrive over the open `/events` stream, so this only acks.
 * Throws {@link ApiError} with 404 (no hand-off pending / already answered), 409
 * (awaiting a clarification instead), or 422 (`person_id` is not the current
 * hand-off target).
 */
export function excludeHandoffCandidate(
  request: HandoffExcludeRequest,
  options: RequestOptions = {},
): Promise<AckResponse> {
  return postJson<AckResponse>("/handoff/exclude", request, options);
}

/**
 * POST /handoff/correct — the asker corrects the AI's interpretation ("解釈の訂正",
 * #260). The `supplement` is folded into the question and the whole pipeline
 * re-runs from C1; the re-run streams over `/events`, so this only acks. Throws
 * {@link ApiError} with 404 (no hand-off pending / already answered), 409
 * (awaiting a clarification instead), or 422 (blank supplement / nothing to
 * correct).
 */
export function correctInterpretation(
  request: HandoffCorrectRequest,
  options: RequestOptions = {},
): Promise<AckResponse> {
  return postJson<AckResponse>("/handoff/correct", request, options);
}

/**
 * POST /handoff/redraft — the asker asks the AI to regenerate the hand-off draft
 * for the current send target ("下書きの作り直し", #260), discarding any saved edit.
 * The new draft arrives over the open `/events` stream, so this only acks. Throws
 * {@link ApiError} with 404 (no hand-off pending / already answered) or 409
 * (awaiting a clarification instead).
 */
export function regenerateHandoffDraft(
  request: HandoffRedraftRequest,
  options: RequestOptions = {},
): Promise<AckResponse> {
  return postJson<AckResponse>("/handoff/redraft", request, options);
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
 * GET /employees — the employee directory for the ADMIN's demo user switcher
 * (admin-only, #241). Returns the unwrapped array (ids in the external "E###"
 * form). Regular users never call this.
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

/**
 * GET /questions — the asker's own recent questions with resolution state
 * (画面1 の "最近のあなたの質問"). `askerId` is the external "E###" form.
 * `limit` caps how many newest-first questions come back (default 5 for the panel;
 * the history screen #208 passes a larger value). Returns the unwrapped items array.
 */
export async function getRecentQuestions(
  askerId: string,
  options: RequestOptions & { limit?: number } = {},
): Promise<RecentQuestionItem[]> {
  const { limit, ...requestOptions } = options;
  const limitQuery = limit === undefined ? "" : `&limit=${encodeURIComponent(limit)}`;
  const query = `?asker_id=${encodeURIComponent(askerId)}${limitQuery}`;
  const body = await getJson<RecentQuestionsResponse>(`/questions${query}`, requestOptions);
  return body.items;
}

/**
 * DELETE /questions/{questionId} — remove one of the asker's own past questions
 * and its history (#207). Only the owning asker (or an admin) may delete; the API
 * answers 403 otherwise and 404 for a missing question. Returns the acknowledgement.
 */
export async function deleteQuestion(
  questionId: string,
  options: RequestOptions = {},
): Promise<DeleteQuestionResponse> {
  return deleteJson<DeleteQuestionResponse>(
    `/questions/${encodeURIComponent(questionId)}`,
    options,
  );
}

/**
 * POST /questions/{questionId}/resolve — mark one of the asker's own questions
 * self-resolved ("人を介さず解決した", #159). Only the owning asker (or an admin) may;
 * 403 otherwise, 404 for a missing question. Idempotent. Returns the ack.
 */
export async function resolveQuestion(
  questionId: string,
  options: RequestOptions = {},
): Promise<ResolveQuestionResponse> {
  return postJson<ResolveQuestionResponse>(
    `/questions/${encodeURIComponent(questionId)}/resolve`,
    {},
    options,
  );
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
      headers: { ...authHeaders() },
      signal: options.signal,
    });
    await response.text();
  } catch {
    // Best-effort: the outcome is already persisted; the asker's own reconnecting
    // stream is the backup consumer. Never surface this to the responder.
  }
}
