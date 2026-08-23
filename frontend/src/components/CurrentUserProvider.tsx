"use client";

/**
 * Current-user context (prototype stand-in for auth).
 *
 * The app has no login yet, so the acting employee is chosen from the directory
 * (`GET /employees`) and remembered in `localStorage`. Both the header switcher
 * (`AppHeader`) and the asker/responder screens read the selection from here, so
 * `asker_id` and the inbox responder id are no longer hardcoded.
 *
 * The default context (no provider) is inert — empty directory, `null` user —
 * so a component can still render in isolation (unit tests); real wiring comes
 * from wrapping the tree in {@link CurrentUserProvider} (done in app/layout.tsx).
 */

import { getEmployees } from "@/lib/api-client";
import type { EmployeeSummary } from "@/lib/api-types";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

const STORAGE_KEY = "tekijin.currentUserId";

export interface CurrentUserContextValue {
  /** The employee directory (empty until loaded / on load failure). */
  employees: EmployeeSummary[];
  /** The acting user's id ("E###"), or null before the directory loads. */
  currentUserId: string | null;
  /** The acting user's full record, or null when unresolved. */
  currentUser: EmployeeSummary | null;
  /** Select a different acting user (persisted to localStorage). */
  setCurrentUserId: (id: string) => void;
  /** True while the directory request is in flight. */
  loading: boolean;
}

const INERT: CurrentUserContextValue = {
  employees: [],
  currentUserId: null,
  currentUser: null,
  setCurrentUserId: () => {},
  loading: false,
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
  const [employees, setEmployees] = useState<EmployeeSummary[]>([]);
  const [currentUserId, setCurrentUserIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const setCurrentUserId = useCallback((id: string) => {
    setCurrentUserIdState(id);
    writeStored(id);
  }, []);

  useEffect(() => {
    let active = true;
    getEmployees()
      .then((list) => {
        if (!active) return;
        setEmployees(list);
        // Restore the remembered user if it still exists, else default to the
        // first employee so the app always has an acting user.
        const stored = readStored();
        const initial =
          stored && list.some((e) => e.id === stored) ? stored : (list[0]?.id ?? null);
        setCurrentUserIdState(initial);
      })
      .catch(() => {
        // Leave the directory empty; the switcher renders a disabled placeholder
        // and screens that need an acting user keep their submit gated.
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const currentUser = useMemo(
    () => employees.find((e) => e.id === currentUserId) ?? null,
    [employees, currentUserId],
  );

  const value = useMemo<CurrentUserContextValue>(
    () => ({ employees, currentUserId, currentUser, setCurrentUserId, loading }),
    [employees, currentUserId, currentUser, setCurrentUserId, loading],
  );

  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}
