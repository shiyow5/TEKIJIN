"use client";

/**
 * Client app shell: authentication + route guard + the header/main chrome (#241).
 *
 * Wraps the whole app in {@link AuthProvider}, then {@link AuthGate} enforces
 * login: unauthenticated visitors are redirected to ``/login`` (which renders
 * bare, without the header), and an authenticated visitor on ``/login`` is sent
 * home. The authenticated app renders inside {@link CurrentUserProvider} with the
 * {@link AppHeader}, exactly the chrome the root layout used to hold.
 */

import { AppHeader } from "@/components/AppHeader";
import { AuthProvider, useAuth } from "@/components/AuthProvider";
import { CurrentUserProvider } from "@/components/CurrentUserProvider";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

const LOGIN_ROUTE = "/login";

function FullScreen({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-margin text-on-surface-variant">
      {children}
    </div>
  );
}

function AuthGate({ children }: { children: ReactNode }) {
  const { principal, loading } = useAuth();
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const onLoginRoute = pathname === LOGIN_ROUTE;

  useEffect(() => {
    if (loading) return;
    if (!principal && !onLoginRoute) {
      router.replace(LOGIN_ROUTE);
    } else if (principal && onLoginRoute) {
      router.replace("/");
    }
  }, [loading, principal, onLoginRoute, router]);

  // The login page renders on its own, without the app chrome.
  if (onLoginRoute) {
    return <>{children}</>;
  }
  if (loading) {
    return <FullScreen>読み込み中…</FullScreen>;
  }
  if (!principal) {
    // Redirecting to /login (effect above) — render nothing meaningful meanwhile.
    return <FullScreen>ログイン画面へ移動しています…</FullScreen>;
  }

  return (
    <CurrentUserProvider>
      <div className="mx-auto flex min-h-screen w-full max-w-content flex-col">
        <AppHeader />
        <main className="flex-1 px-margin py-lg">{children}</main>
      </div>
    </CurrentUserProvider>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AuthGate>{children}</AuthGate>
    </AuthProvider>
  );
}
