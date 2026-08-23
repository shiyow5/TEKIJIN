/**
 * User-facing labels for the C5 route enum (`stream.route.route`).
 *
 * The backend emits raw enums — `person` / `prior_answer` / `document` / `none`
 * — which must never surface verbatim in the UI (they read as internal jargon,
 * e.g. "経路: prior_answer"). Unknown values fall back to the raw string so a new
 * route still renders rather than showing nothing.
 */

export const ROUTE_LABELS: Record<string, string> = {
  person: "人に聞く",
  prior_answer: "過去の回答",
  document: "社内文書",
  none: "該当なし",
};

export function routeLabel(route: string): string {
  return ROUTE_LABELS[route] ?? route;
}
