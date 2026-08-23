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
  | { session_id: string; outcome: Outcome; reply?: never }
  | { session_id: string; reply: string; outcome?: never };

/** Acknowledgement returned by /ask and /answer (the stream flows over /events). */
export interface AckResponse {
  session_id: string;
  status: string;
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
  created_at?: string | null;
}

/** GET /questions payload (schemas.py `RecentQuestionsResponse`). */
export interface RecentQuestionsResponse {
  items: RecentQuestionItem[];
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

/**
 * GET /dashboard payload (schemas.py `DashboardResponse`). Aggregate-only — no
 * per-record listing (product-spec §241-251: summarise usage, never a monitoring
 * log of individual questions).
 */
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
  /** 推薦精度: latest offline eval snapshot; null when not yet measured. */
  latest_eval: EvalSnapshot | null;
  answers_per_responder: ResponderLoad[];
  topic_distribution: TopicCount[];
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
}

export interface MessageData {
  status: string;
  message: string;
  /** For the "document" route: the cited document's id (GET /documents/{doc_id}). */
  doc_id?: string | null;
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
