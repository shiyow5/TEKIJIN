/**
 * User-facing labels for the C5 route enum (`stream.route.route`).
 *
 * The backend `Route` type is `Literal["person", "prior_answer", "document"]`
 * (backend/src/tekijin/agent/state.py); these raw enums must never surface
 * verbatim in the UI (they read as internal jargon, e.g. "経路: prior_answer").
 * Any unexpected value falls back to the raw string via `routeLabel`, so a
 * future route still renders rather than showing nothing.
 */

export const ROUTE_LABELS: Record<string, string> = {
  person: "人に聞く",
  prior_answer: "過去の回答",
  document: "社内文書",
};

export function routeLabel(route: string): string {
  return ROUTE_LABELS[route] ?? route;
}
