"use client";

/**
 * Current-user (acting employee) context, now driven by authentication (#241).
 *
 * The acting user depends on WHO is logged in ({@link useAuth}):
 *
 * * A regular USER acts only as themselves — `currentUserId` is their own id, the
 *   directory is empty, and switching is disabled. There is no impersonation.
 * * The ADMIN gets the demo switcher: the employee directory (`GET /employees`,
 *   admin-only) loads and `currentUserId` is the chosen impersonation (remembered
 *   in `localStorage`, defaulting to the first employee).
 *
 * The context shape is unchanged, so the asker/responder screens (`asker_id`,
 * inbox `responder_id`) read the acting id from here exactly as before.
 */

import { useAuth } from "@/components/AuthProvider";
import { getEmployees } from "@/lib/api-client";
import type { EmployeeSummary } from "@/lib/api-types";
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

const STORAGE_KEY = "tekijin.currentUserId";

export interface CurrentUserContextValue {
  /** The employee directory (admin only; empty for a regular user). */
  employees: EmployeeSummary[];
  /** The acting user's id ("E###"), or null before it resolves. */
  currentUserId: string | null;
  /** The acting user's full record, or null when unresolved. */
  currentUser: EmployeeSummary | null;
  /** Select a different acting user (admin only; no-op for a regular user). */
  setCurrentUserId: (id: string) => void;
  /** True while the admin directory request is in flight. */
  loading: boolean;
  /** True when the last directory load failed (admin only). */
  error: boolean;
  /** Retry loading the directory (admin only). */
  reload: () => void;
  /** True when the acting user may switch identities (admin). */
  canSwitch: boolean;
}

const INERT: CurrentUserContextValue = {
  employees: [],
  currentUserId: null,
  currentUser: null,
  setCurrentUserId: () => {},
  loading: false,
  error: false,
  reload: () => {},
  canSwitch: false,
};

const CurrentUserContext = createContext<CurrentUserContextValue>(INERT);

export function useCurrentUser(): CurrentUserContextValue {
  return useContext(CurrentUserContext);
}

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStored(id: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Private-mode / disabled storage — selection just won't persist.
  }
}

export function CurrentUserProvider({ children }: { children: ReactNode }) {
  const { principal } = useAuth();
  const isAdmin = principal?.is_admin === true;

  const [employees, setEmployees] = useState<EmployeeSummary[]>([]);
  const [adminSelectedId, setAdminSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(isAdmin);
  const [error, setError] = useState(false);

  const setCurrentUserId = useCallback(
    (id: string) => {
      if (!isAdmin) return; // regular users cannot switch identity
      setAdminSelectedId(id);
      writeStored(id);
    },
    [isAdmin],
  );

  // Admin only: load the directory so the switcher can impersonate anyone.
  const load = useCallback(
    (isActive: () => boolean = () => true) => {
      if (!isAdmin) return;
      setLoading(true);
      setError(false);
      getEmployees()
        .then((list) => {
          if (!isActive()) return;
          setEmployees(list);
          const stored = readStored();
          const initial =
            stored && list.some((e) => e.id === stored) ? stored : (list[0]?.id ?? null);
          setAdminSelectedId(initial);
        })
        .catch(() => {
          if (isActive()) setError(true);
        })
        .finally(() => {
          if (isActive()) setLoading(false);
        });
    },
    [isAdmin],
  );

  const reload = useCallback(() => load(), [load]);

  useEffect(() => {
    if (!isAdmin) {
      // Regular user (or logged out): no directory, act as self.
      setEmployees([]);
      setAdminSelectedId(null);
      setLoading(false);
      setError(false);
      return;
    }
    let active = true;
    load(() => active);
    return () => {
      active = false;
    };
  }, [isAdmin, load]);

  // A regular user's acting identity is fixed to their own principal.
  const selfUser = useMemo<EmployeeSummary | null>(() => {
    if (!principal || principal.is_admin || principal.id === null) return null;
    return { id: principal.id, name: principal.name, dept: principal.dept ?? null };
  }, [principal]);

  const currentUserId = isAdmin ? adminSelectedId : (selfUser?.id ?? null);
  const currentUser = useMemo<EmployeeSummary | null>(() => {
    if (isAdmin) return employees.find((e) => e.id === currentUserId) ?? null;
    return selfUser;
  }, [isAdmin, employees, currentUserId, selfUser]);

  const value = useMemo<CurrentUserContextValue>(
    () => ({
      employees,
      currentUserId,
      currentUser,
      setCurrentUserId,
      loading,
      error,
      reload,
      canSwitch: isAdmin,
    }),
    [employees, currentUserId, currentUser, setCurrentUserId, loading, error, reload, isAdmin],
  );

  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}
