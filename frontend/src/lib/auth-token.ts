/**
 * Access-token holder shared by the API client and the SSE hook.
 *
 * The token is kept both in a module-level variable (so `api-client` and
 * `useEventStream` can read it synchronously without prop-drilling) and in
 * `localStorage` (so a page reload restores the session). `AuthProvider` is the
 * single writer via {@link setAuthToken}; everything else only reads.
 */

const STORAGE_KEY = "tekijin.authToken";

let current: string | null = null;

/** Load the persisted token into the in-memory cache (called once on startup). */
export function loadStoredToken(): string | null {
  try {
    current = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    current = null;
  }
  return current;
}

/** The current access token, or null when logged out. */
export function getAuthToken(): string | null {
  return current;
}

/** Set (or clear, with null) the token, mirroring it to localStorage. */
export function setAuthToken(token: string | null): void {
  current = token;
  try {
    if (token === null) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, token);
    }
  } catch {
    // Private-mode / disabled storage — the in-memory token still works for this
    // tab; it just won't survive a reload.
  }
}
