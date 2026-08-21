/**
 * Session id generation and validation.
 *
 * `session_id` doubles as the `/events/{session_id}` path segment and the graph
 * `thread_id`, so the backend constrains it to path-safe characters
 * (schemas.py `_SESSION_ID_PATTERN`). We generate and validate against the same
 * pattern here so a created session is always reachable over GET /events.
 */

/** Same character class the backend enforces: `^[A-Za-z0-9_-]+$`. */
export const SESSION_ID_PATTERN = /^[A-Za-z0-9_-]+$/;

export function isValidSessionId(value: string): boolean {
  return SESSION_ID_PATTERN.test(value);
}

/**
 * Create a fresh, path-safe session id. A v4 UUID uses only hex digits and
 * hyphens, which are all inside the allowed pattern. Falls back to a
 * timestamp+random id when `crypto.randomUUID` is unavailable.
 */
export function createSessionId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  const random = Math.random().toString(36).slice(2);
  return `s-${Date.now().toString(36)}-${random}`;
}
