/**
 * TypeScript mirror of the backend API contract.
 *
 * Source of truth: `backend/src/tekijin/api/schemas.py` (Pydantic v2 models) and
 * `backend/src/tekijin/api/events.py` (SSE event mapping). Kept in sync by hand;
 * the shapes below match those models field-for-field.
 *
 * Only `postAsk` (POST /ask) is wired in #35. The SSE data types are defined
 * ahead of time so the /events subscription hook (#36) can reuse them.
 */

// --------------------------------------------------------------------------- //
// requests / responses
// --------------------------------------------------------------------------- //

/**
 * Employee id as accepted by the API boundary. The DB stores it as an `int`, but
 * the external contract also accepts the spec's `"E###"` string form
 * (schemas.py `_coerce_asker_id`). The frontend normally sends the numeric form.
 */
export type EmployeeId = number | string;

/** POST /ask body — start (or restart) a question for a session. */
export interface AskRequest {
  asker_id: EmployeeId;
  question: string;
  session_id: string;
}

/**
 * POST /answer body — resume a paused run with exactly one of `outcome`
 * (responder accept/decline) or `reply` (clarification answer). Defined for
 * later screens; #35 does not call /answer.
 */
export type Outcome = "accepted" | "declined";

/**
 * Exactly one of `outcome` or `reply` — encoded as a discriminated union so a
 * caller (the #38 answer screen) cannot construct a payload with both or neither,
 * matching the backend's `_exactly_one` validator. The `never` on the unused arm
 * makes the wrong field a compile error rather than a runtime 422.
 */
export type ResumeRequest =
  | { session_id: string; outcome: Outcome; recommendation_id?: number | null; reply?: never }
  | { session_id: string; reply: string; outcome?: never; recommendation_id?: never };

/** Acknowledgement returned by /ask and /answer (the stream flows over /events). */
export interface AckResponse {
  session_id: string;
  status: string;
}

/** DELETE /questions/{id} — acknowledgement that a past question was removed (#207). */
export interface DeleteQuestionResponse {
  question_id: string;
  deleted: boolean;
}

/**
 * POST /handoff/draft body — persist the asker's edited hand-off draft (画面3) so
 * the responder (画面4) reads the edited text. Draft-only; it never changes the
 * recommendation or the accept/decline outcome (#174).
 */
export interface HandoffDraftRequest {
  session_id: string;
  draft: string;
}

/**
 * POST /handoff/select body — the asker picks a different (of the currently
 * shown) candidate as the hand-off target; the draft is regenerated for them
 * (#200/#A1/#204).
 */
export interface HandoffSelectRequest {
  session_id: string;
  person_id: EmployeeId;
}

/**
 * POST /handoff/exclude body — the asker excludes the current send target
 * ("この人には聞かない"), rerouting to a freshly-scored next candidate (#260).
 * `person_id` must be the current hand-off target. The new candidate + draft
 * arrive over the open `/events` stream, so the response only acks.
 */
export interface HandoffExcludeRequest {
  session_id: string;
  person_id: EmployeeId;
}

/**
 * POST /handoff/correct body — the asker corrects the AI's interpretation
 * ("解釈の訂正", #260). The `supplement` is folded into the question and the whole
 * pipeline re-runs from C1; the re-run streams over `/events`, so the response
 * only acks.
 */
export interface HandoffCorrectRequest {
  session_id: string;
  supplement: string;
}

/**
 * POST /handoff/redraft body — the asker asks the AI to regenerate the hand-off
 * draft for the current send target ("下書きの作り直し", #260), discarding any saved
 * edit. The new draft arrives over the open `/events` stream, so the response
 * only acks.
 */
export interface HandoffRedraftRequest {
  session_id: string;
}

/** POST /handoff/select response (schemas.py `HandoffSelectResponse`). */
export interface HandoffSelectResponse {
  session_id: string;
  responder: Recommendation;
  draft: string;
  recommendation_id: number;
}

/**
 * One employee for the current-user switcher (GET /employees). `id` is the
 * external "E###" form — the same shape accepted back as `asker_id` and used as
 * the responder id for the inbox, so a selection round-trips without conversion.
 */
export interface EmployeeSummary {
  id: string;
  name: string;
  dept?: string | null;
}

/** GET /employees payload (schemas.py `EmployeeListResponse`). */
export interface EmployeeListResponse {
  employees: EmployeeSummary[];
}

/**
 * One pending handoff awaiting the responder (GET /inbox, schemas.py `InboxItem`).
 * `session_id` deep-links to `/answer/{session_id}`.
 */
export interface InboxItem {
  session_id: string;
  question_id: string;
  question: string;
  topics: string[];
  asker: HandoffAsker;
  created_at?: string | null;
}

/** GET /inbox payload (schemas.py `InboxResponse`). */
export interface InboxResponse {
  items: InboxItem[];
}

/**
 * One of the asker's own recent questions (GET /questions, schemas.py
 * `RecentQuestionItem`). `responder_name` is the accepting/answering person, or
 * null while unresolved.
 */
/**
 * How a question was resolved (or that it is still pending): "person" a responder
 * took it, "document" the document route self-resolved it (no human), "pending"
 * still awaiting a hand-off.
 */
export type QuestionResolution = "person" | "document" | "pending";

export interface RecentQuestionItem {
  question_id: string;
  title: string;
  resolved: boolean;
  resolution: QuestionResolution;
  responder_name?: string | null;
  /** Deep-link target for re-viewing the result (/session/{session_id}); null for seeded history. */
  session_id?: string | null;
  created_at?: string | null;
}

/** GET /questions payload (schemas.py `RecentQuestionsResponse`). */
export interface RecentQuestionsResponse {
  items: RecentQuestionItem[];
}

/**
 * One decline event the asker hasn't acknowledged yet (GET /notifications,
 * schemas.py `DeclineNotification`). Paired with the automatic reroute
 * (#206): by the time this is shown, the system has already moved on to
 * the next candidate — it is informational, not a request to act (#E7).
 */
export interface DeclineNotification {
  /** the declined Recommendation row's id — also the ack target. */
  id: number;
  question_id: string;
  /** deep-link target; null for pre-session-tracking rows. */
  session_id?: string | null;
  message: string;
  declined_person_name: string;
  created_at?: string | null;
}

/** GET /notifications payload (schemas.py `NotificationsResponse`). */
export interface NotificationsResponse {
  items: DeclineNotification[];
}

/** POST /notifications/ack body — mark decline notifications as seen (#E7). */
export interface NotificationAckRequest {
  asker_id: EmployeeId;
  ids: number[];
}

/** POST /notifications/ack response (schemas.py `NotificationAckResponse`). */
export interface NotificationAckResponse {
  acknowledged: number;
}

// --------------------------------------------------------------------------- //
// domain models (shared by SSE data and final response)
// --------------------------------------------------------------------------- //

export interface Reason {
  type: string;
  detail: string;
}

export interface Recommendation {
  /** external "E###" form (schemas.format_employee_id). */
  person_id: string;
  name: string;
  dept?: string | null;
  score: number;
  confidence: string;
  reasons: Reason[];
}

// --------------------------------------------------------------------------- //
// handoff (GET /handoff/{session_id}) — responder-facing view (product-spec 画面4)
// --------------------------------------------------------------------------- //

/** The asking employee, enriched for the responder-facing handoff view. */
export interface HandoffAsker {
  /** external "E###" form (schemas.format_employee_id). */
  id: string;
  name?: string | null;
  dept?: string | null;
}

/**
 * Responder-facing payload for a session paused at the `send` interrupt
 * (schemas.py `HandoffResponse`). Read-only: fetching it does not advance the
 * graph — the responder acts via POST /answer (outcome=accepted|declined).
 */
export interface HandoffResponse {
  session_id: string;
  question: string;
  asker: HandoffAsker;
  topics: string[];
  products: string[];
  situation?: string | null;
  missing: string[];
  /** the primary (handed-off) candidate — the person being asked. */
  responder?: Recommendation | null;
  draft: string;
  reuse_count: number;
  helpful_answer_count: number;
  /** Generation token echoed back on POST /answer so a stale outcome 409s (#94). */
  recommendation_id?: number | null;
}

// --------------------------------------------------------------------------- //
// dashboard (GET /dashboard) — aggregate-only view (product-spec 画面5)
// --------------------------------------------------------------------------- //

export interface ResponderLoad {
  employee_id: number;
  name: string;
  answer_count: number;
}

export interface TopicCount {
  topic: string;
  count: number;
}

export interface OutcomeCounts {
  accepted: number;
  declined: number;
  pending: number;
}

/** Latest stored offline-evaluation metrics (推薦精度); null until `make eval` ran. */
export interface EvalSnapshot {
  top1_accuracy: number | null;
  recall_at_3: number | null;
  mrr: number | null;
  route_accuracy: number | null;
  created_at: string | null;
}

/** p50/p95 of per-question AI processing time in ms (schemas.py `ProcessingLatency`, #177). */
export interface ProcessingLatency {
  p50_ms: number | null;
  p95_ms: number | null;
  sample_size: number;
}

/**
 * GET /dashboard payload (schemas.py `DashboardResponse`). Aggregate-only — no
 * per-record listing (product-spec §241-251: summarise usage, never a monitoring
 * log of individual questions).
 */
/** #237: feedback counts per pipeline stage (どの段でどれだけずれているか). */
export interface FeedbackByStage {
  c1: number;
  c6: number;
  c7: number;
  total: number;
}

export interface DashboardResponse {
  total_employees: number;
  total_questions: number;
  total_answers: number;
  recommendation_count: number;
  recommendation_outcomes: OutcomeCounts;
  acceptance_rate: number;
  /** 自己解決率: fraction routed to an auxiliary route (prior_answer/document). */
  self_resolution_rate: number;
  /** 平均解決時間 in hours; null when nothing is resolved yet. */
  avg_resolution_hours: number | null;
  /** 負荷分散: share of answers by the single busiest responder. */
  top_responder_share: number;
  /** 応答速度: p50/p95 of AI processing time (ms) from recorded run events (#177). */
  processing_latency: ProcessingLatency;
  /** 推薦精度: latest offline eval snapshot; null when not yet measured. */
  latest_eval: EvalSnapshot | null;
  answers_per_responder: ResponderLoad[];
  topic_distribution: TopicCount[];
  /** #237: how often the asking side corrected each stage (c1/c6/c7). */
  feedback_by_stage: FeedbackByStage;
}

// --------------------------------------------------------------------------- //
// SSE event data (GET /events/{session_id}) — mirrors events.py
// --------------------------------------------------------------------------- //

export interface UnderstoodData {
  topics: string[];
  products: string[];
  situation?: string | null;
  question_type?: string | null;
  confidence: number;
}

export interface FollowupData {
  question: string;
  missing: string[];
}

export interface RouteData {
  route: string;
  reason: string;
  confidence: number;
}

export interface RecommendData {
  recommendations: Recommendation[];
}

export interface DraftData {
  draft: string;
}

export interface DoneData {
  status: string;
  answer?: string | null;
  /** This run segment's processing time in ms (#177); null on a replay. */
  latency_ms?: number | null;
}

export interface MessageData {
  status: string;
  message: string;
  /** For the "document" route: the cited document's id (GET /documents/{doc_id}). */
  doc_id?: string | null;
  /** This run segment's processing time in ms (#177); null on a replay. */
  latency_ms?: number | null;
}

/** GET /documents/{doc_id} payload (schemas.py `DocumentDetail`). */
export interface DocumentDetail {
  id: string;
  title?: string | null;
  body?: string | null;
  source?: string | null;
  updated_at?: string | null;
}

export interface ErrorData {
  error: string;
}

/**
 * Named SSE events emitted over /events, mapped to their `data` payload type.
 * (understood/followup/route/recommend/draft/done/message/error — see events.py.)
 */
export interface SseEventDataMap {
  understood: UnderstoodData;
  followup: FollowupData;
  route: RouteData;
  recommend: RecommendData;
  draft: DraftData;
  done: DoneData;
  message: MessageData;
  error: ErrorData;
}

export type SseEventName = keyof SseEventDataMap;

/**
 * The authenticated principal (GET /auth/me, POST /auth/login). `id` is the
 * external "E###" form for a regular user, or `null` for the admin (which is not
 * a DB employee). `is_admin` gates the dashboard and the demo user switcher.
 */
export interface Principal {
  id: string | null;
  name: string;
  dept?: string | null;
  is_admin: boolean;
}

/** POST /auth/login request body. */
export interface LoginRequest {
  email: string;
  password: string;
}

/** POST /auth/login response (schemas.py `LoginResponse`). */
export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  principal: Principal;
}
