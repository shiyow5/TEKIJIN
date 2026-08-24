"use client";

/**
 * Authentication context (#241).
 *
 * Holds the current {@link Principal} (or null when logged out) and the login/
 * logout actions. On mount it restores a persisted token and calls `GET /auth/me`
 * to rehydrate the principal, so a page reload keeps the session. The token itself
 * lives in {@link auth-token} (read synchronously by the API client and SSE hook);
 * this provider is its single writer.
 *
 * The default (no provider) is inert — logged out, not loading — so a component
 * can render in isolation in unit tests.
 */

import { getMe, postLogin, postLogout } from "@/lib/api-client";
import type { Principal } from "@/lib/api-types";
import { loadStoredToken, setAuthToken } from "@/lib/auth-token";
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export interface AuthContextValue {
  /** The signed-in principal, or null when logged out. */
  principal: Principal | null;
  /** True while the initial session-restore (`GET /auth/me`) is in flight. */
  loading: boolean;
  /** Exchange credentials for a token; sets the principal. Throws on failure. */
  login: (email: string, password: string) => Promise<void>;
  /** Clear the token and principal (best-effort server logout). */
  logout: () => Promise<void>;
}

const INERT: AuthContextValue = {
  principal: null,
  loading: false,
  login: async () => {},
  logout: async () => {},
};

const AuthContext = createContext<AuthContextValue>(INERT);

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);

  // Restore a persisted session once on mount.
  useEffect(() => {
    let active = true;
    const token = loadStoredToken();
    if (!token) {
      setLoading(false);
      return;
    }
    getMe()
      .then((me) => {
        if (active) setPrincipal(me);
      })
      .catch(() => {
        // Expired/invalid token — drop it and stay logged out.
        setAuthToken(null);
        if (active) setPrincipal(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await postLogin({ email, password });
    setAuthToken(result.access_token);
    setPrincipal(result.principal);
  }, []);

  const logout = useCallback(async () => {
    await postLogout();
    setAuthToken(null);
    setPrincipal(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ principal, loading, login, logout }),
    [principal, loading, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
