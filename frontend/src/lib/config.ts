/**
 * Runtime configuration resolved from environment variables.
 *
 * The API base URL is injected at build time via `NEXT_PUBLIC_API_BASE_URL`
 * (Next.js inlines `NEXT_PUBLIC_*` into the client bundle). Keep it centralised
 * here so no component hardcodes a URL. Falls back to the local dev server.
 */

export const DEFAULT_API_BASE_URL = "http://localhost:8000";

/** Trim trailing slashes so callers can safely concatenate `${base}/ask`. */
export function normalizeBaseUrl(base: string): string {
  return base.replace(/\/+$/, "");
}

/**
 * Resolve the API base URL, trimming any trailing slash so callers can safely
 * concatenate `${base}/ask`. Read at call time (not module load) so tests can
 * override `process.env.NEXT_PUBLIC_API_BASE_URL`.
 */
export function getApiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL;
  const base = raw && raw.trim() !== "" ? raw.trim() : DEFAULT_API_BASE_URL;
  return normalizeBaseUrl(base);
}
